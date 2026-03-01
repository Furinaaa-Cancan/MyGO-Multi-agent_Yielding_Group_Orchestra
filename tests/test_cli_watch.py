"""Task 38: Tests for ma watch command."""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _mock_app(has_next=True, task_id="task-w"):
    app = MagicMock()
    snapshot = MagicMock()
    snapshot.values = {"current_role": "builder", "builder_id": "w", "final_status": "approved"}
    snapshot.next = ["build"] if has_next else []
    snapshot.tasks = [MagicMock(interrupts=[MagicMock(value={"role": "builder", "agent": "w"})])] if has_next else []
    app.get_state = MagicMock(return_value=snapshot)
    return app


class TestWatchCommand:
    def test_no_active_task(self, runner):
        app = MagicMock()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value=None):
            result = runner.invoke(main, ["watch"])
        assert result.exit_code != 0
        assert "No active task" in result.output

    def test_lock_mismatch(self, runner):
        app = _mock_app()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-a"), \
             patch("multi_agent.cli.read_lock", return_value="task-b"):
            result = runner.invoke(main, ["watch"])
        assert result.exit_code != 0

    def test_already_finished(self, runner):
        app = _mock_app(has_next=False)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-done"), \
             patch("multi_agent.cli.read_lock", return_value="task-done"), \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.clear_runtime"):
            result = runner.invoke(main, ["watch"])
        assert result.exit_code == 0
        assert "finished" in result.output or "already" in result.output.lower()

    def test_watch_starts_loop(self, runner):
        app = _mock_app(has_next=True)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-w"), \
             patch("multi_agent.cli.read_lock", return_value="task-w"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop") as rwl:
            result = runner.invoke(main, ["watch"])
        assert result.exit_code == 0
        rwl.assert_called_once()

    def test_interval_param(self, runner):
        app = _mock_app(has_next=True)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-w"), \
             patch("multi_agent.cli.read_lock", return_value="task-w"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop") as rwl:
            result = runner.invoke(main, ["watch", "--interval", "5"])
        assert result.exit_code == 0
        rwl.assert_called_once()
        assert rwl.call_args[1].get("interval") == 5.0 or rwl.call_args[0][-1] == 5.0
