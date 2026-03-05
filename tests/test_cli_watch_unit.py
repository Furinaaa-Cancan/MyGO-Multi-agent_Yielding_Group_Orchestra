"""Unit tests for cli_watch.py — _normalize_resume_output, _handle_terminal, _show_next_agent, _process_outbox."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from multi_agent.cli_watch import (
    _handle_terminal,
    _normalize_resume_output,
    _process_outbox,
    _show_next_agent,
    _show_waiting,
)


# ── _normalize_resume_output ─────────────────────────────


class TestNormalizeResumeOutput:
    """Test payload normalization for reviewer output."""

    def test_builder_role_returns_data_unchanged(self):
        data = {"summary": "done", "status": "completed"}
        result = _normalize_resume_output("builder", data, {})
        assert result == data

    def test_pass_normalized_to_approve(self):
        data = {"decision": "pass"}
        result = _normalize_resume_output("reviewer", data, {})
        assert result["decision"] == "approve"

    def test_fail_normalized_to_reject(self):
        data = {"decision": "fail"}
        result = _normalize_resume_output("reviewer", data, {})
        assert result["decision"] == "reject"

    def test_approve_kept_as_is(self):
        data = {"decision": "approve", "evidence": ["test passed"]}
        result = _normalize_resume_output("reviewer", data, {})
        assert result["decision"] == "approve"

    def test_strict_mode_requires_evidence(self):
        data = {"decision": "approve"}
        state = {"workflow_mode": "strict"}
        with pytest.raises(ValueError, match="evidence"):
            _normalize_resume_output("reviewer", data, state)

    def test_strict_mode_with_evidence_passes(self):
        data = {"decision": "approve", "evidence": ["unit tests pass"]}
        state = {"workflow_mode": "strict"}
        result = _normalize_resume_output("reviewer", data, state)
        assert result["decision"] == "approve"

    def test_evidence_files_count_toward_minimum(self):
        data = {"decision": "approve", "evidence_files": ["report.md"]}
        state = {"workflow_mode": "strict"}
        result = _normalize_resume_output("reviewer", data, state)
        assert result["decision"] == "approve"

    def test_review_policy_overrides_evidence_requirement(self):
        data = {"decision": "approve"}
        state = {
            "workflow_mode": "normal",
            "review_policy": {
                "reviewer": {"require_evidence_on_approve": True, "min_evidence_items": 2},
            },
        }
        with pytest.raises(ValueError, match="need >= 2"):
            _normalize_resume_output("reviewer", data, state)

    def test_review_policy_disable_evidence(self):
        data = {"decision": "approve"}
        state = {
            "workflow_mode": "strict",
            "review_policy": {
                "reviewer": {"require_evidence_on_approve": False},
            },
        }
        result = _normalize_resume_output("reviewer", data, state)
        assert result["decision"] == "approve"

    def test_reject_no_evidence_needed(self):
        data = {"decision": "reject", "feedback": "needs work"}
        state = {"workflow_mode": "strict"}
        result = _normalize_resume_output("reviewer", data, state)
        assert result["decision"] == "reject"

    def test_non_dict_review_policy_ignored(self):
        data = {"decision": "approve", "evidence": ["ok"]}
        state = {"workflow_mode": "strict", "review_policy": "invalid"}
        result = _normalize_resume_output("reviewer", data, state)
        assert result["decision"] == "approve"

    def test_non_dict_reviewer_cfg_ignored(self):
        data = {"decision": "approve", "evidence": ["ok"]}
        state = {"workflow_mode": "strict", "review_policy": {"reviewer": "bad"}}
        result = _normalize_resume_output("reviewer", data, state)
        assert result["decision"] == "approve"


# ── _handle_terminal ─────────────────────────────────────


class TestHandleTerminal:
    def _make_status(self, final: str, error: str = "", values: dict | None = None):
        s = SimpleNamespace()
        s.final_status = final
        s.error = error
        s.values = values or {}
        return s

    def test_approved_shows_success(self, capsys):
        status = self._make_status("approved", values={"builder_output": {"summary": "All done"}})
        _handle_terminal(status, "t-1", "00:10", manage_lock=False)
        out = capsys.readouterr().out
        assert "✅" in out
        assert "All done" in out

    def test_approved_with_retries(self, capsys):
        status = self._make_status("approved", values={"retry_count": 2})
        _handle_terminal(status, "t-1", "01:00", manage_lock=False)
        out = capsys.readouterr().out
        assert "2" in out

    def test_failed_shows_error(self, capsys):
        status = self._make_status("failed", error="timeout")
        _handle_terminal(status, "t-1", "00:30", manage_lock=False)
        out = capsys.readouterr().out
        assert "❌" in out
        assert "timeout" in out

    @patch("multi_agent.cli_watch.release_lock")
    @patch("multi_agent.cli_watch.clear_runtime")
    @patch("multi_agent.cli_watch.save_task_yaml")
    def test_manage_lock_releases(self, mock_save, mock_clear, mock_release):
        status = self._make_status("done")
        _handle_terminal(status, "t-1", "00:05", manage_lock=True)
        mock_release.assert_called_once()
        mock_clear.assert_called_once()

    @patch("multi_agent.cli_watch.release_lock")
    @patch("multi_agent.cli_watch.clear_runtime")
    @patch("multi_agent.cli_watch.save_task_yaml")
    def test_no_manage_lock_skips_release(self, mock_save, mock_clear, mock_release):
        status = self._make_status("done")
        _handle_terminal(status, "t-1", "00:05", manage_lock=False)
        mock_release.assert_not_called()
        mock_clear.assert_not_called()


# ── _show_waiting ────────────────────────────────────────


class TestShowWaiting:
    def _make_status(self, is_terminal: bool, final: str = "", waiting_role: str = "builder",
                     waiting_agent: str = "windsurf", error: str = "", values: dict | None = None):
        s = SimpleNamespace()
        s.is_terminal = is_terminal
        s.final_status = final
        s.error = error
        s.waiting_role = waiting_role
        s.waiting_agent = waiting_agent
        s.values = values or {}
        return s

    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""})
    @patch("multi_agent.driver.can_use_cli", return_value=False)
    @patch("multi_agent.driver.spawn_cli_agent")
    def test_terminal_approved(self, mock_spawn, mock_cli, mock_drv, capsys):
        status = self._make_status(is_terminal=True, final="approved")
        with patch("multi_agent.orchestrator.get_task_status", return_value=status):
            app = MagicMock()
            _show_waiting(app, {"configurable": {"thread_id": "t-1"}})
        out = capsys.readouterr().out
        assert "✅" in out

    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""})
    @patch("multi_agent.driver.can_use_cli", return_value=False)
    @patch("multi_agent.driver.spawn_cli_agent")
    def test_terminal_failed(self, mock_spawn, mock_cli, mock_drv, capsys):
        status = self._make_status(is_terminal=True, final="failed", error="boom")
        with patch("multi_agent.orchestrator.get_task_status", return_value=status):
            app = MagicMock()
            _show_waiting(app, {"configurable": {"thread_id": "t-1"}})
        out = capsys.readouterr().out
        assert "❌" in out
        assert "boom" in out

    @patch("multi_agent.driver.spawn_cli_agent")
    @patch("multi_agent.driver.can_use_cli", return_value=True)
    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "cli", "command": "windsurf run"})
    def test_cli_agent_auto_spawned(self, mock_drv, mock_cli, mock_spawn):
        status = self._make_status(is_terminal=False, waiting_agent="windsurf",
                                   values={"timeout_sec": 300})
        with patch("multi_agent.orchestrator.get_task_status", return_value=status):
            app = MagicMock()
            _show_waiting(app, {"configurable": {"thread_id": "t-1"}})
        mock_spawn.assert_called_once()

    @patch("multi_agent.driver.spawn_cli_agent")
    @patch("multi_agent.driver.can_use_cli", return_value=False)
    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "cli", "command": "windsurf run"})
    def test_cli_not_installed_degrades_to_manual(self, mock_drv, mock_cli, mock_spawn, capsys):
        status = self._make_status(is_terminal=False, waiting_agent="windsurf")
        with patch("multi_agent.orchestrator.get_task_status", return_value=status):
            app = MagicMock()
            _show_waiting(app, {"configurable": {"thread_id": "t-1"}})
        mock_spawn.assert_not_called()
        out = capsys.readouterr().out
        assert "未安装" in out or "降级" in out

    @patch("multi_agent.driver.spawn_cli_agent")
    @patch("multi_agent.driver.can_use_cli", return_value=False)
    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""})
    def test_file_driver_shows_manual_instructions(self, mock_drv, mock_cli, mock_spawn, capsys):
        status = self._make_status(is_terminal=False, waiting_agent="cursor")
        with patch("multi_agent.orchestrator.get_task_status", return_value=status):
            app = MagicMock()
            _show_waiting(app, {"configurable": {"thread_id": "t-1"}})
        mock_spawn.assert_not_called()
        out = capsys.readouterr().out
        assert "TASK.md" in out


# ── _show_next_agent ─────────────────────────────────────


class TestShowNextAgent:
    @patch("multi_agent.driver.spawn_cli_agent")
    @patch("multi_agent.driver.can_use_cli", return_value=True)
    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "cli", "command": "ws run"})
    def test_retry_feedback_shown(self, mock_drv, mock_cli, mock_spawn, capsys):
        status = SimpleNamespace(
            waiting_role="builder", waiting_agent="windsurf",
            values={"retry_count": 1, "retry_budget": 2,
                    "reviewer_output": {"feedback": "Add tests"}, "timeout_sec": 600},
        )
        _show_next_agent(status, "01:00")
        out = capsys.readouterr().out
        assert "1/2" in out
        assert "Add tests" in out

    @patch("multi_agent.driver.spawn_cli_agent")
    @patch("multi_agent.driver.can_use_cli", return_value=False)
    @patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""})
    def test_manual_mode_for_reviewer(self, mock_drv, mock_cli, mock_spawn, capsys):
        status = SimpleNamespace(
            waiting_role="reviewer", waiting_agent="cursor",
            values={"retry_count": 0},
        )
        _show_next_agent(status, "02:00")
        out = capsys.readouterr().out
        assert "TASK.md" in out


# ── _process_outbox ──────────────────────────────────────


class TestProcessOutbox:
    def _make_status(self, values: dict | None = None):
        s = SimpleNamespace()
        s.values = values or {}
        s.is_terminal = False
        s.waiting_role = "builder"
        s.waiting_agent = "windsurf"
        return s

    @patch("multi_agent.cli_watch.release_lock")
    @patch("multi_agent.cli_watch.clear_runtime")
    @patch("multi_agent.cli_watch.save_task_yaml")
    @patch("multi_agent.cli_watch.validate_outbox_data", return_value=[])
    def test_matching_role_resumes(self, mock_val, mock_save, mock_clear, mock_rel, capsys):
        poller = MagicMock()
        poller.check_once.return_value = [("builder", {"summary": "done", "status": "completed"})]

        next_status = SimpleNamespace(
            is_terminal=False, waiting_role="reviewer", waiting_agent="cursor",
            values={"retry_count": 0},
        )
        with patch("multi_agent.orchestrator.resume_task", return_value=next_status), \
             patch("multi_agent.cli_watch._show_next_agent"):
            result = _process_outbox(poller, "builder", "ws", self._make_status(), MagicMock(), "t-1", "00:10", True)
        assert result == "continue"

    @patch("multi_agent.cli_watch.release_lock")
    @patch("multi_agent.cli_watch.clear_runtime")
    @patch("multi_agent.cli_watch.save_task_yaml")
    @patch("multi_agent.cli_watch.validate_outbox_data", return_value=[])
    def test_resume_error_returns_return(self, mock_val, mock_save, mock_clear, mock_rel, capsys):
        poller = MagicMock()
        poller.check_once.return_value = [("builder", {"summary": "done", "status": "completed"})]

        with patch("multi_agent.orchestrator.resume_task", side_effect=RuntimeError("boom")):
            result = _process_outbox(poller, "builder", "ws", self._make_status(), MagicMock(), "t-1", "00:10", True)
        assert result == "return"
        mock_rel.assert_called_once()

    def test_no_matching_role_continues(self, capsys):
        poller = MagicMock()
        poller.check_once.return_value = [("reviewer", {"decision": "approve"})]

        result = _process_outbox(poller, "builder", "ws", self._make_status(), MagicMock(), "t-1", "00:10", True)
        assert result == "continue"

    def test_empty_outbox_continues(self):
        poller = MagicMock()
        poller.check_once.return_value = []

        result = _process_outbox(poller, "builder", "ws", self._make_status(), MagicMock(), "t-1", "00:10", True)
        assert result == "continue"

    @patch("multi_agent.cli_watch.validate_outbox_data", return_value=[])
    def test_normalize_error_continues_loop(self, mock_val, capsys):
        poller = MagicMock()
        poller.check_once.return_value = [("reviewer", {"decision": "approve"})]
        status = self._make_status(values={"workflow_mode": "strict"})

        # This should trigger ValueError from _normalize_resume_output
        result = _process_outbox(poller, "reviewer", "cursor", status, MagicMock(), "t-1", "00:10", True)
        # normalize fails → continues (doesn't return)
        assert result == "continue"
