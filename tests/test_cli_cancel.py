"""Task 36: Tests for ma cancel command."""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _mock_app(active_task_id=None):
    app = MagicMock()
    return app


class TestCancelCommand:
    def test_no_active_task(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.read_lock", return_value=None):
            result = runner.invoke(main, ["cancel"])
        assert result.exit_code == 0
        assert "No active task" in result.output

    def test_cancel_active_task(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli._detect_active_task", return_value="task-abc"), \
             patch("multi_agent.cli.save_task_yaml") as sty, \
             patch("multi_agent.cli.release_lock") as rl, \
             patch("multi_agent.cli.clear_runtime") as cr:
            result = runner.invoke(main, ["cancel"])
        assert result.exit_code == 0
        assert "task-abc" in result.output
        assert "cancelled" in result.output
        rl.assert_called_once()
        cr.assert_called_once()
        sty.assert_called_once()
        saved = sty.call_args[0][1]
        assert saved["status"] == "cancelled"

    def test_cancel_with_task_id(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.save_task_yaml") as sty, \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.clear_runtime"):
            result = runner.invoke(main, ["cancel", "--task-id", "task-xyz"])
        assert result.exit_code == 0
        assert "task-xyz" in result.output

    def test_cancel_with_reason(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli._detect_active_task", return_value="task-abc"), \
             patch("multi_agent.cli.save_task_yaml") as sty, \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.clear_runtime"):
            result = runner.invoke(main, ["cancel", "--reason", "wrong requirements"])
        assert result.exit_code == 0
        assert "wrong requirements" in result.output
        saved = sty.call_args[0][1]
        assert saved["reason"] == "wrong requirements"

    def test_orphaned_lock_detected(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.read_lock", return_value="task-orphan"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli.release_lock") as rl, \
             patch("multi_agent.cli.clear_runtime"):
            result = runner.invoke(main, ["cancel"])
        assert result.exit_code == 0
        assert "task-orphan" in result.output
        rl.assert_called_once()

    def test_release_lock_called(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli._detect_active_task", return_value="task-x"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli.release_lock") as rl, \
             patch("multi_agent.cli.clear_runtime"):
            runner.invoke(main, ["cancel"])
        rl.assert_called_once()

    def test_task_yaml_status_cancelled(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli._detect_active_task", return_value="task-x"), \
             patch("multi_agent.cli.save_task_yaml") as sty, \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.clear_runtime"):
            runner.invoke(main, ["cancel"])
        data = sty.call_args[0][1]
        assert data["status"] == "cancelled"
