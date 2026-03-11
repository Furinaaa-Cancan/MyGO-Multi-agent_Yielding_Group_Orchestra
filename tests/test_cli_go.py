"""Task 34: Tests for ma go command parameter combinations."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _mock_app():
    app = MagicMock()
    app.invoke = MagicMock(side_effect=_raise_graph_interrupt)
    snapshot = MagicMock()
    snapshot.next = ["build"]
    snapshot.tasks = [MagicMock(interrupts=[MagicMock(value={"role": "builder", "agent": "windsurf"})])]
    snapshot.values = {"current_role": "builder", "builder_id": "windsurf"}
    app.get_state = MagicMock(return_value=snapshot)
    return app


def _mock_app_terminal(final_status="failed"):
    app = MagicMock()
    snapshot = MagicMock()
    snapshot.next = []
    snapshot.tasks = []
    snapshot.values = {"final_status": final_status}
    app.get_state = MagicMock(return_value=snapshot)
    return app


def _raise_graph_interrupt(*a, **kw):
    from langgraph.errors import GraphInterrupt
    raise GraphInterrupt()


class TestGoCommand:
    def test_basic_invocation(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, ["go", "implement login", "--no-watch"])
        assert result.exit_code == 0
        assert "Task" in result.output

    def test_with_builder_reviewer(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, [
                "go", "add auth", "--builder", "windsurf", "--reviewer", "cursor", "--no-watch"
            ])
        assert result.exit_code == 0

    def test_with_skill(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, [
                "go", "write tests", "--skill", "test-and-review", "--no-watch"
            ])
        assert result.exit_code == 0

    def test_with_custom_task_id(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, [
                "go", "fix bug", "--task-id", "task-custom-01", "--no-watch"
            ])
        assert result.exit_code == 0
        assert "task-custom-01" in result.output

    def test_retry_budget(self, runner):
        app = _mock_app()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, [
                "go", "do thing", "--retry-budget", "5", "--no-watch"
            ])
        assert result.exit_code == 0
        # Check invoke was called with retry_budget=5
        call_args = app.invoke.call_args[0][0]
        assert call_args["retry_budget"] == 5

    def test_timeout(self, runner):
        app = _mock_app()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, [
                "go", "do thing", "--timeout", "900", "--no-watch"
            ])
        assert result.exit_code == 0
        call_args = app.invoke.call_args[0][0]
        assert call_args["timeout_sec"] == 900

    def test_injects_workflow_mode_and_review_policy(self, runner):
        app = _mock_app()
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, ["go", "do thing", "--mode", "strict", "--no-watch"])
        assert result.exit_code == 0
        call_args = app.invoke.call_args[0][0]
        assert call_args["workflow_mode"] == "strict"
        assert isinstance(call_args["review_policy"], dict)
        assert call_args["review_policy"]["reviewer"]["require_evidence_on_approve"] is True

    def test_active_task_blocks(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value="task-existing"):
            result = runner.invoke(main, ["go", "new task"])
        assert result.exit_code != 0
        assert "正在进行中" in result.output or "task-existing" in result.output

    def test_stale_lock_is_auto_cleaned(self, runner):
        app = _mock_app_terminal("failed")
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value="task-stale"), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.release_lock") as rel, \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop"):
            result = runner.invoke(main, ["go", "new task", "--no-watch"])
        assert result.exit_code == 0
        assert "陈旧锁" in result.output
        rel.assert_called_once()

    def test_active_marker_without_lock_blocks(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value="task-orphan"), \
             patch("multi_agent.cli.acquire_lock"):
            result = runner.invoke(main, ["go", "new task"])
        assert result.exit_code != 0
        assert "活跃任务标记" in result.output or "task-orphan" in result.output

    def test_skill_not_found(self, runner):
        app = MagicMock()
        app.invoke = MagicMock(side_effect=FileNotFoundError("Skill 'nonexistent' not found"))
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli.release_lock"):
            result = runner.invoke(main, ["go", "test", "--skill", "nonexistent", "--no-watch"])
        assert result.exit_code != 0
        assert "nonexistent" in result.output or "Skill" in result.output

    def test_no_agents_error(self, runner):
        app = MagicMock()
        app.invoke = MagicMock(side_effect=ValueError("No agents configured"))
        with patch("multi_agent.graph.compile_graph", return_value=app), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli.release_lock"):
            result = runner.invoke(main, ["go", "test", "--no-watch"])
        assert result.exit_code != 0

    def test_no_watch_flag(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli.save_task_yaml"), \
             patch("multi_agent.cli._show_waiting"), \
             patch("multi_agent.cli._run_watch_loop") as rwl:
            result = runner.invoke(main, ["go", "test", "--no-watch"])
        assert result.exit_code == 0
        rwl.assert_not_called()

    def test_go_fails_fast_when_agent_not_ready(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli._resolve_and_validate_agents_for_run", side_effect=Exception("builder not ready")):
            result = runner.invoke(main, ["go", "test"])
        assert result.exit_code != 0
        assert "builder not ready" in result.output

    def test_decompose_flag(self, runner):
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli_decompose._run_decomposed") as rd:
            result = runner.invoke(main, ["go", "complex task", "--decompose", "--no-watch"])
        assert result.exit_code == 0
        rd.assert_called_once()

    def test_builder_equals_reviewer_error(self, runner):
        result = runner.invoke(main, ["go", "test req", "--builder", "windsurf", "--reviewer", "windsurf"])
        assert result.exit_code != 0

    def test_decompose_file_yaml(self, runner, tmp_path):
        """T29: --decompose-file should accept YAML format."""
        yaml_file = tmp_path / "decompose.yaml"
        yaml_file.write_text(
            'sub_tasks:\n  - id: step-1\n    description: "Do step 1"\n    deps: []\nreasoning: test\n'
        )
        with patch("multi_agent.graph.compile_graph", return_value=_mock_app()), \
             patch("multi_agent.cli.ensure_workspace"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.cli.acquire_lock"), \
             patch("multi_agent.cli_decompose._run_decomposed") as rd:
            result = runner.invoke(main, [
                "go", "test", "--decompose-file", str(yaml_file),
                "--task-id", "task-yaml-test",
            ])
        assert result.exit_code == 0
        rd.assert_called_once()

    def test_missing_requirement(self, runner):
        result = runner.invoke(main, ["go"])
        assert result.exit_code != 0
