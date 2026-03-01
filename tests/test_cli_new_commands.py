"""Tests for new CLI commands (Tasks 87/93/96/97/98/99/100)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Set up a temp project root with skills and agents dirs."""
    from multi_agent.config import root_dir
    (tmp_path / "skills").mkdir()
    (tmp_path / "agents").mkdir()
    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    root_dir.cache_clear()
    yield tmp_path
    monkeypatch.delenv("MA_ROOT", raising=False)
    root_dir.cache_clear()


class TestCacheStats:
    """Task 87: ma cache-stats command."""

    def test_cache_stats_output(self, runner, tmp_root):
        result = runner.invoke(main, ["cache-stats"])
        assert result.exit_code == 0
        assert "root_dir cache" in result.output
        assert "hits=" in result.output


class TestSchemaCommand:
    """Task 93: ma schema command."""

    def test_schema_all(self, runner):
        result = runner.invoke(main, ["schema", "all"])
        assert result.exit_code == 0
        assert "Task" in result.output
        assert "BuilderOutput" in result.output
        assert "properties" in result.output

    def test_schema_single_model(self, runner):
        result = runner.invoke(main, ["schema", "Task"])
        assert result.exit_code == 0
        assert "properties" in result.output
        assert "task_id" in result.output

    def test_schema_subtask(self, runner):
        result = runner.invoke(main, ["schema", "SubTask"])
        assert result.exit_code == 0
        assert "description" in result.output


class TestVersionCommand:
    """Task 100: ma version command."""

    def test_version_output(self, runner):
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "AgentOrchestra" in result.output
        assert "Python" in result.output


class TestInitCommand:
    """Task 75: ma init command."""

    def test_init_creates_structure(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            with patch("multi_agent.cli.ensure_workspace"):
                result = runner.invoke(main, ["init"])
            assert result.exit_code == 0
            assert "初始化完成" in result.output

    def test_init_already_initialized(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            Path("skills").mkdir()
            Path("agents").mkdir()
            result = runner.invoke(main, ["init"])
            assert "已初始化" in result.output

    def test_init_force(self, runner, tmp_path):
        with runner.isolated_filesystem(temp_dir=tmp_path) as td:
            Path("skills").mkdir()
            Path("agents").mkdir()
            with patch("multi_agent.cli.ensure_workspace"):
                result = runner.invoke(main, ["init", "--force"])
            assert result.exit_code == 0
            assert "初始化完成" in result.output


class TestDoctorCommand:
    """Task 73: ma doctor command."""

    def test_doctor_healthy(self, runner, tmp_root):
        from multi_agent.workspace import ensure_workspace
        ensure_workspace()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "Workspace" in result.output


class TestAgentsCommand:
    """Task 97: ma agents command."""

    def test_agents_shows_list(self, runner, tmp_root):
        from multi_agent.schema import AgentProfile
        agents = [AgentProfile(id="ws", capabilities=["implementation"], reliability=0.9)]
        with patch("multi_agent.router.load_agents", return_value=agents), \
             patch("multi_agent.router.check_agent_health",
                   return_value=[{"id": "ws", "status": "healthy", "issues": []}]):
            result = runner.invoke(main, ["agents"])
        assert result.exit_code == 0
        assert "ws" in result.output


class TestListSkillsCommand:
    """Task 96: ma list-skills command."""

    def test_list_skills(self, runner, tmp_root):
        skill_dir = tmp_root / "skills" / "code-implement"
        skill_dir.mkdir(parents=True)
        import yaml
        contract = {"id": "code-implement", "version": "1.0.0",
                     "description": "Implement code", "quality_gates": ["lint"]}
        (skill_dir / "contract.yaml").write_text(
            yaml.dump(contract), encoding="utf-8"
        )
        result = runner.invoke(main, ["list-skills"])
        assert result.exit_code == 0
        assert "code-implement" in result.output
        assert "lint" in result.output

    def test_list_skills_empty(self, runner, tmp_root):
        result = runner.invoke(main, ["list-skills"])
        assert "暂无" in result.output


class TestExportCommand:
    """Task 98: ma export command."""

    def test_export_json(self, runner, tmp_root):
        from multi_agent.workspace import ensure_workspace
        from multi_agent.config import history_dir
        ensure_workspace()
        hd = history_dir()
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "task-abc.json").write_text(
            json.dumps([{"role": "builder", "action": "build"}])
        )
        result = runner.invoke(main, ["export", "task-abc"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["task_id"] == "task-abc"
        assert len(data["conversation"]) == 1

    def test_export_markdown(self, runner, tmp_root):
        from multi_agent.workspace import ensure_workspace
        from multi_agent.config import history_dir
        ensure_workspace()
        hd = history_dir()
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "task-abc.json").write_text(
            json.dumps([{"role": "builder", "action": "build"}])
        )
        result = runner.invoke(main, ["export", "task-abc", "--format", "markdown"])
        assert result.exit_code == 0
        assert "# Task: task-abc" in result.output


class TestReplayCommand:
    """Task 99: ma replay command."""

    def test_replay_task(self, runner, tmp_root):
        from multi_agent.workspace import ensure_workspace
        from multi_agent.config import history_dir
        ensure_workspace()
        hd = history_dir()
        hd.mkdir(parents=True, exist_ok=True)
        convo = [
            {"role": "builder", "action": "build", "summary": "did stuff"},
            {"role": "reviewer", "action": "review"},
        ]
        (hd / "task-xyz.json").write_text(json.dumps(convo))
        result = runner.invoke(main, ["replay", "task-xyz"])
        assert result.exit_code == 0
        assert "Replay" in result.output
        assert "builder" in result.output

    def test_replay_from_step(self, runner, tmp_root):
        from multi_agent.workspace import ensure_workspace
        from multi_agent.config import history_dir
        ensure_workspace()
        hd = history_dir()
        hd.mkdir(parents=True, exist_ok=True)
        convo = [{"role": f"r{i}", "action": f"a{i}"} for i in range(5)]
        (hd / "task-step.json").write_text(json.dumps(convo))
        result = runner.invoke(main, ["replay", "task-step", "--from-step", "3"])
        assert result.exit_code == 0
        assert "[3]" in result.output
        assert "[0]" not in result.output

    def test_replay_missing_history(self, runner, tmp_root):
        from multi_agent.workspace import ensure_workspace
        ensure_workspace()
        result = runner.invoke(main, ["replay", "nonexistent"])
        assert result.exit_code != 0


class TestCleanupCommand:
    """Task 92: ma cleanup command."""

    def test_cleanup_command(self, runner, tmp_root):
        with patch("multi_agent.workspace.cleanup_old_files", return_value=3):
            result = runner.invoke(main, ["cleanup"])
        assert result.exit_code == 0
        assert "3" in result.output


class TestRenderCommand:
    """Regression test for render command — Task(requirement=...) bug fix."""

    def test_render_builder_prompt(self, runner, tmp_root):
        # Create a minimal skill contract
        import yaml
        skill_dir = tmp_root / "skills" / "code-implement"
        skill_dir.mkdir(parents=True, exist_ok=True)
        contract = {
            "id": "code-implement", "version": "1.0.0",
            "description": "test", "quality_gates": [],
        }
        (skill_dir / "contract.yaml").write_text(
            yaml.dump(contract), encoding="utf-8")
        result = runner.invoke(main, ["render", "test requirement", "--skill", "code-implement"])
        assert result.exit_code == 0
        assert "render-preview" in result.output or "test requirement" in result.output

    def test_render_reviewer_requires_builder_output(self, runner, tmp_root):
        import yaml
        skill_dir = tmp_root / "skills" / "code-implement"
        skill_dir.mkdir(parents=True, exist_ok=True)
        contract = {
            "id": "code-implement", "version": "1.0.0",
            "description": "test", "quality_gates": [],
        }
        (skill_dir / "contract.yaml").write_text(
            yaml.dump(contract), encoding="utf-8")
        result = runner.invoke(main, ["render", "test", "--role", "reviewer"])
        assert result.exit_code != 0
        assert "builder-output" in result.output.lower() or result.exit_code == 1


class TestVersionConsistency:
    """Task 100: Version consistency tests."""

    def test_init_version_matches_pyproject(self):
        from multi_agent import __version__
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        if pyproject.exists():
            content = pyproject.read_text()
            assert f'version = "{__version__}"' in content
