"""Task 37: Tests for ma status command."""

from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _mock_app(vals=None, has_next=True):
    app = MagicMock()
    snapshot = MagicMock()
    snapshot.values = vals or {
        "current_role": "builder", "builder_id": "windsurf",
        "reviewer_id": "cursor", "retry_count": 0, "retry_budget": 2,
    }
    snapshot.next = ["build"] if has_next else []
    if has_next:
        snapshot.tasks = [MagicMock(interrupts=[MagicMock(value={"role": "builder", "agent": "windsurf"})])]
    else:
        snapshot.tasks = []
    app.get_state = MagicMock(return_value=snapshot)
    return app


class TestStatusCommand:
    def test_no_active_task(self, runner):
        app = MagicMock()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value=None):
            result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "No active tasks" in result.output

    def test_builder_waiting(self, runner):
        app = _mock_app()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-abc"), \
             patch("multi_agent.cli.read_lock", return_value="task-abc"), \
             patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""}):
            result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "task-abc" in result.output
        assert "builder" in result.output.lower() or "Builder" in result.output
        assert "windsurf" in result.output

    def test_reviewer_waiting(self, runner):
        vals = {
            "current_role": "reviewer", "builder_id": "windsurf",
            "reviewer_id": "cursor", "retry_count": 1, "retry_budget": 2,
        }
        app = _mock_app(vals=vals)
        app.get_state.return_value.tasks = [
            MagicMock(interrupts=[MagicMock(value={"role": "reviewer", "agent": "cursor"})])
        ]
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-abc"), \
             patch("multi_agent.cli.read_lock", return_value="task-abc"), \
             patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""}):
            result = runner.invoke(main, ["status"])
        assert result.exit_code == 0
        assert "reviewer" in result.output.lower()
        assert "cursor" in result.output

    def test_shows_error(self, runner):
        vals = {
            "current_role": "builder", "builder_id": "w", "reviewer_id": "c",
            "retry_count": 0, "retry_budget": 2, "error": "timeout occurred",
        }
        app = _mock_app(vals=vals, has_next=False)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-err"), \
             patch("multi_agent.cli.read_lock", return_value=None):
            result = runner.invoke(main, ["status"])
        assert "timeout occurred" in result.output

    def test_shows_final_status(self, runner):
        vals = {
            "current_role": "builder", "builder_id": "w", "reviewer_id": "c",
            "retry_count": 0, "retry_budget": 2, "final_status": "approved",
        }
        app = _mock_app(vals=vals, has_next=False)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-ok"), \
             patch("multi_agent.cli.read_lock", return_value=None):
            result = runner.invoke(main, ["status"])
        assert "approved" in result.output

    def test_retry_count_displayed(self, runner):
        vals = {
            "current_role": "builder", "builder_id": "w", "reviewer_id": "c",
            "retry_count": 2, "retry_budget": 3,
        }
        app = _mock_app(vals=vals)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-r"), \
             patch("multi_agent.cli.read_lock", return_value="task-r"), \
             patch("multi_agent.driver.get_agent_driver", return_value={"driver": "file", "command": ""}):
            result = runner.invoke(main, ["status"])
        assert "2/3" in result.output

    def test_lock_status_shown(self, runner):
        app = _mock_app(has_next=False)
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli._detect_active_task", return_value="task-x"), \
             patch("multi_agent.cli.read_lock", return_value="task-x"):
            result = runner.invoke(main, ["status"])
        assert "task-x" in result.output
