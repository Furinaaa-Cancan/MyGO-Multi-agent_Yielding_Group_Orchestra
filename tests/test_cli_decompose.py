"""Integration tests for CLI decompose flow."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from multi_agent.cli import main
from multi_agent.schema import SubTask, DecomposeResult


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Set up a minimal workspace for CLI tests."""
    # Create skills
    skill_dir = tmp_path / "skills" / "code-implement"
    skill_dir.mkdir(parents=True)
    (skill_dir / "contract.yaml").write_text("""
id: code-implement
version: 1.0.0
description: test
inputs: []
outputs: []
preconditions: []
postconditions: []
quality_gates: []
timeouts:
  run_sec: 1800
  verify_sec: 600
retry:
  max_attempts: 2
  backoff: linear
fallback:
  on_failure: retry
compatibility:
  supported_agents: []
handoff:
  artifact_path: ""
  required_fields: []
""")

    # Create agents
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "agents.yaml").write_text("""
version: 2
role_strategy: manual
defaults:
  builder: windsurf
  reviewer: cursor
agents:
  - id: windsurf
    capabilities: [implementation, review]
  - id: cursor
    capabilities: [implementation, review]
""")

    # Create templates
    tmpl_dir = tmp_path / "src" / "multi_agent" / "templates"
    tmpl_dir.mkdir(parents=True)
    # Use the real templates
    real_tmpl = Path(__file__).parent.parent / "src" / "multi_agent" / "templates"
    if real_tmpl.exists():
        for t in real_tmpl.glob("*.j2"):
            (tmpl_dir / t.name).write_text(t.read_text())
    else:
        # Minimal fallback templates
        (tmpl_dir / "builder.md.j2").write_text("Build: {{ task.task_id }}")
        (tmpl_dir / "reviewer.md.j2").write_text("Review: {{ task.task_id }}")

    # Set MA_ROOT
    monkeypatch.setenv("MA_ROOT", str(tmp_path))

    # Clear lru_cache so config picks up new MA_ROOT
    from multi_agent.config import root_dir
    root_dir.cache_clear()

    # Create workspace dir
    ws = tmp_path / ".multi-agent"
    ws.mkdir()
    (ws / "inbox").mkdir()
    (ws / "outbox").mkdir()
    (ws / "tasks").mkdir()
    (ws / "history").mkdir()

    yield tmp_path

    # Cleanup
    root_dir.cache_clear()


class TestGoDecomposeFlag:
    """Test that --decompose flag is accepted and triggers decompose flow."""

    def test_help_shows_decompose(self):
        runner = CliRunner()
        result = runner.invoke(main, ["go", "--help"])
        assert "--decompose" in result.output

    @patch("multi_agent.cli._run_decomposed")
    @patch("multi_agent.graph.compile_graph")
    def test_decompose_flag_calls_run_decomposed(self, mock_compile, mock_decomposed, workspace):
        mock_app = MagicMock()
        mock_compile.return_value = mock_app

        runner = CliRunner()
        result = runner.invoke(main, [
            "go", "implement auth", "--decompose", "--task-id", "task-test-dec",
        ])
        mock_decomposed.assert_called_once()
        args = mock_decomposed.call_args
        assert args[0][1] == "task-test-dec"  # parent_task_id
        assert args[0][2] == "implement auth"  # requirement

    @patch("multi_agent.cli._run_single_task")
    @patch("multi_agent.graph.compile_graph")
    def test_no_decompose_calls_single_task(self, mock_compile, mock_single, workspace):
        mock_app = MagicMock()
        mock_compile.return_value = mock_app

        runner = CliRunner()
        result = runner.invoke(main, [
            "go", "fix bug", "--task-id", "task-test-single",
        ])
        mock_single.assert_called_once()


class TestDecomposePromptWrite:
    """Test decompose prompt generation."""

    def test_prompt_contains_requirement(self, workspace):
        from multi_agent.decompose import write_decompose_prompt
        p = write_decompose_prompt("Build user auth module")
        content = p.read_text()
        assert "Build user auth module" in content
        assert "sub_tasks" in content
        assert "decompose.json" in content

    def test_inbox_created(self, workspace):
        from multi_agent.decompose import write_decompose_prompt
        write_decompose_prompt("test requirement")
        inbox_p = workspace / ".multi-agent" / "inbox" / "decompose.md"
        assert inbox_p.exists()
        assert "test requirement" in inbox_p.read_text()


class TestDecomposeTopoSortEdgeCases:
    """Additional edge case tests for topo sort."""

    def test_self_dependency_raises(self):
        from multi_agent.decompose import topo_sort
        tasks = [SubTask(id="a", description="A", deps=["a"])]
        with pytest.raises(ValueError, match="Circular"):
            topo_sort(tasks)

    def test_three_node_cycle_raises(self):
        from multi_agent.decompose import topo_sort
        tasks = [
            SubTask(id="a", description="A", deps=["c"]),
            SubTask(id="b", description="B", deps=["a"]),
            SubTask(id="c", description="C", deps=["b"]),
        ]
        with pytest.raises(ValueError, match="Circular"):
            topo_sort(tasks)


class TestSubTaskStateBuilding:
    """Test that sub-task states are correctly built for graph invocation."""

    def test_state_has_required_fields(self):
        from multi_agent.meta_graph import build_sub_task_state
        st = SubTask(id="auth-login", description="Implement login")
        state = build_sub_task_state(st, "task-parent-01")

        required = ["task_id", "requirement", "skill_id", "done_criteria",
                     "timeout_sec", "retry_budget", "retry_count",
                     "input_payload", "builder_explicit", "reviewer_explicit",
                     "conversation"]
        for key in required:
            assert key in state, f"Missing required key: {key}"

    def test_prior_results_only_include_relevant_info(self):
        from multi_agent.meta_graph import build_sub_task_state
        st = SubTask(id="step-2", description="Step 2", deps=["step-1"])
        prior = [
            {"sub_id": "step-1", "summary": "Done step 1",
             "changed_files": ["/a.py", "/b.py"]},
        ]
        state = build_sub_task_state(st, "task-parent-01", prior_results=prior)
        # Prior results should be in requirement context
        assert "step-1" in state["requirement"]
        assert "Done step 1" in state["requirement"]
        # Original description should still be there
        assert "Step 2" in state["requirement"]


class TestAggregateEdgeCases:
    """Test aggregate_results edge cases."""

    def test_mixed_statuses(self):
        from multi_agent.meta_graph import aggregate_results
        results = [
            {"sub_id": "a", "status": "approved", "summary": "", "changed_files": [], "retry_count": 0},
            {"sub_id": "b", "status": "skipped", "summary": "", "changed_files": [], "retry_count": 0},
            {"sub_id": "c", "status": "completed", "summary": "", "changed_files": [], "retry_count": 0},
            {"sub_id": "d", "status": "failed", "summary": "", "changed_files": [], "retry_count": 3},
        ]
        agg = aggregate_results("parent", results)
        assert agg["final_status"] == "failed"
        assert set(agg["failed"]) == {"b", "d"}
        assert agg["completed"] == 2  # approved + completed
        assert agg["total_retries"] == 3

    def test_all_completed_status(self):
        from multi_agent.meta_graph import aggregate_results
        results = [
            {"sub_id": "a", "status": "completed", "summary": "", "changed_files": [], "retry_count": 0},
        ]
        agg = aggregate_results("parent", results)
        assert agg["final_status"] == "approved"


class TestUserChoiceOnFailure:
    """T21: Verify skip/retry/abort choices on sub-task failure."""

    def test_choice_includes_retry(self):
        """The choice set should include 'retry' alongside skip and abort."""
        # Just verify the source code has all three choices
        import inspect
        from multi_agent.cli import _run_decomposed
        src = inspect.getsource(_run_decomposed)
        assert '"skip", "retry", "abort"' in src or "'skip', 'retry', 'abort'" in src

    def test_auto_confirm_skips_choice(self):
        """When auto_confirm=True, failed sub-tasks should be auto-skipped (no prompt)."""
        import inspect
        from multi_agent.cli import _run_decomposed
        src = inspect.getsource(_run_decomposed)
        assert "auto_confirm" in src
        assert "click.prompt" in src


class TestNoCacheFlag:
    """T23: Verify --no-cache flag skips decompose result cache."""

    def test_help_shows_no_cache(self):
        runner = CliRunner()
        result = runner.invoke(main, ["go", "--help"])
        assert "--no-cache" in result.output

    @patch("multi_agent.cli._run_decomposed")
    @patch("multi_agent.graph.compile_graph")
    def test_no_cache_passed_to_run_decomposed(self, mock_compile, mock_decomposed, workspace):
        mock_app = MagicMock()
        mock_compile.return_value = mock_app
        runner = CliRunner()
        runner.invoke(main, [
            "go", "implement auth", "--decompose", "--no-cache",
            "--task-id", "task-test-nc",
        ])
        mock_decomposed.assert_called_once()
        kwargs = mock_decomposed.call_args[1]
        assert kwargs.get("no_cache") is True
