"""Tests for ma done command (Task 35)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from multi_agent.cli import main


def _mock_snapshot(*, has_next=True, role="builder", agent="windsurf", final_status=None):
    """Build a mock StateSnapshot for done command tests."""
    snap = MagicMock()
    snap.next = ("build",) if has_next else ()
    interrupt = MagicMock()
    interrupt.value = {"role": role, "agent": agent}
    task = MagicMock()
    task.interrupts = [interrupt]
    snap.tasks = [task]
    vals = {"final_status": final_status} if final_status else {}
    snap.values = vals
    return snap


class TestDoneCommand:
    """Task 35: ma done command tests."""

    def test_no_active_task(self, tmp_path):
        runner = CliRunner()
        with patch("multi_agent.graph.compile_graph") as mock_cg, \
             patch("multi_agent.cli._detect_active_task", return_value=None):
            mock_cg.return_value = MagicMock()
            result = runner.invoke(main, ["done"])
            assert result.exit_code != 0
            assert "No active task" in result.output

    def test_no_pending_interrupt(self, tmp_path):
        runner = CliRunner()
        app = MagicMock()
        snap = _mock_snapshot(has_next=False)
        app.get_state.return_value = snap
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"):
            result = runner.invoke(main, ["done"])
            assert result.exit_code != 0
            assert "No pending interrupt" in result.output

    def test_file_not_valid_json(self, tmp_path):
        runner = CliRunner()
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json", encoding="utf-8")
        app = MagicMock()
        snap = _mock_snapshot()
        app.get_state.return_value = snap
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"):
            result = runner.invoke(main, ["done", "--file", str(bad_file)])
            assert result.exit_code != 0
            assert "Invalid JSON" in result.output

    def test_file_valid_json_submits(self, tmp_path):
        runner = CliRunner()
        good_file = tmp_path / "good.json"
        good_file.write_text(json.dumps({"status": "completed", "summary": "ok"}), encoding="utf-8")
        app = MagicMock()
        snap = _mock_snapshot()
        app.get_state.return_value = snap
        from langgraph.errors import GraphInterrupt
        app.invoke.side_effect = GraphInterrupt()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"), \
             patch("multi_agent.cli._show_waiting"):
            result = runner.invoke(main, ["done", "--file", str(good_file)])
            assert "Submitting" in result.output

    def test_outbox_auto_detect(self, tmp_path):
        runner = CliRunner()
        app = MagicMock()
        snap = _mock_snapshot(role="builder")
        app.get_state.return_value = snap
        from langgraph.errors import GraphInterrupt
        app.invoke.side_effect = GraphInterrupt()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"), \
             patch("multi_agent.cli.read_outbox", return_value={"status": "completed", "summary": "done"}), \
             patch("multi_agent.cli._show_waiting"):
            result = runner.invoke(main, ["done"])
            assert "Submitting builder" in result.output

    def test_no_output_found(self, tmp_path):
        runner = CliRunner()
        app = MagicMock()
        snap = _mock_snapshot(role="builder")
        app.get_state.return_value = snap
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"), \
             patch("multi_agent.cli.read_outbox", return_value=None):
            result = runner.invoke(main, ["done"], input="")
            assert "No output found" in result.output

    def test_task_id_explicit(self, tmp_path):
        runner = CliRunner()
        app = MagicMock()
        snap = _mock_snapshot()
        app.get_state.return_value = snap
        from langgraph.errors import GraphInterrupt
        app.invoke.side_effect = GraphInterrupt()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.read_outbox", return_value={"status": "completed"}), \
             patch("multi_agent.cli._show_waiting"):
            result = runner.invoke(main, ["done", "--task-id", "task-custom"])
            assert "task-custom" in result.output

    def test_reviewer_approve_requires_evidence_in_strict(self, tmp_path):
        runner = CliRunner()
        good_file = tmp_path / "review.json"
        good_file.write_text(
            json.dumps({"decision": "approve", "summary": "Reviewed and approved."}),
            encoding="utf-8",
        )
        app = MagicMock()
        snap = _mock_snapshot(role="reviewer", agent="antigravity")
        snap.values = {
            "workflow_mode": "strict",
            "review_policy": {"reviewer": {"require_evidence_on_approve": True, "min_evidence_items": 1}},
        }
        app.get_state.return_value = snap
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"):
            result = runner.invoke(main, ["done", "--file", str(good_file)])
        assert result.exit_code != 0
        assert "reviewer approve requires evidence" in result.output

    def test_reviewer_pass_alias_maps_to_approve(self, tmp_path):
        runner = CliRunner()
        good_file = tmp_path / "review-pass.json"
        good_file.write_text(
            json.dumps({"decision": "pass", "summary": "Reviewed", "evidence": ["unit tests"]}),
            encoding="utf-8",
        )
        app = MagicMock()
        snap = _mock_snapshot(role="reviewer", agent="antigravity")
        snap.values = {"workflow_mode": "strict"}
        app.get_state.return_value = snap
        from langgraph.errors import GraphInterrupt
        app.invoke.side_effect = GraphInterrupt()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-1"), \
             patch("multi_agent.cli._show_waiting"):
            result = runner.invoke(main, ["done", "--file", str(good_file)])
        assert result.exit_code == 0
        assert "Submitting reviewer output" in result.output
