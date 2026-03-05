"""Tests for cli_admin.py — admin/info CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "skills").mkdir()
    (tmp_path / "agents").mkdir()
    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    from multi_agent.config import root_dir

    root_dir.cache_clear()
    yield tmp_path
    root_dir.cache_clear()


# ── history command (lines 58-59, 75) ────────────────────


class TestHistoryCommand:
    def test_empty_tasks_dir(self, runner, workspace):
        result = runner.invoke(main, ["history"])
        assert result.exit_code == 0
        assert "暂无" in result.output

    def test_corrupt_yaml_skipped(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        (td / "good.yaml").write_text(yaml.dump({"task_id": "good", "status": "approved"}))
        (td / "bad.yaml").write_text(":::\nbad: [yaml")
        result = runner.invoke(main, ["history"])
        assert result.exit_code == 0
        assert "good" in result.output

    def test_no_tasks_with_filter(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        (td / "t1.yaml").write_text(yaml.dump({"task_id": "t1", "status": "approved"}))
        result = runner.invoke(main, ["history", "--status", "failed"])
        assert result.exit_code == 0
        assert "暂无 status=failed" in result.output

    def test_no_tasks_empty_dir(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        # No yaml files at all
        result = runner.invoke(main, ["history"])
        assert result.exit_code == 0
        assert "暂无" in result.output


# ── render-prompt command (lines 162-164, 180-182) ───────


class TestRenderPromptCommand:
    def test_skill_not_found(self, runner, workspace):
        with patch("multi_agent.contract.load_contract", side_effect=FileNotFoundError):
            result = runner.invoke(main, ["render", "test requirement", "--skill", "nonexistent"])
        assert result.exit_code != 0

    def test_reviewer_without_builder_output(self, runner, workspace):
        from multi_agent.schema import SkillContract
        contract = SkillContract(
            id="test-skill", version="1.0", description="test",
            input_schema={}, output_schema={}, quality_gates=[],
        )
        with patch("multi_agent.contract.load_contract", return_value=contract):
            result = runner.invoke(main, [
                "render", "test requirement", "--skill", "test-skill",
                "--role", "reviewer",
            ])
        assert result.exit_code != 0

    def test_reviewer_with_builder_output(self, runner, workspace, tmp_path):
        from multi_agent.schema import SkillContract
        contract = SkillContract(
            id="test-skill", version="1.0", description="test",
            input_schema={}, output_schema={}, quality_gates=[],
        )
        bo_file = tmp_path / "builder_output.json"
        bo_file.write_text(json.dumps({"summary": "done", "changed_files": []}))
        with patch("multi_agent.contract.load_contract", return_value=contract), \
             patch("multi_agent.prompt.render_reviewer_prompt", return_value="reviewer prompt"):
            result = runner.invoke(main, [
                "render", "test requirement", "--skill", "test-skill",
                "--role", "reviewer",
                "--builder-output", str(bo_file),
            ])
        assert result.exit_code == 0
        assert "reviewer prompt" in result.output


# ── doctor command (lines 256, 261-263, 273) ─────────────


class TestDoctorCommand:
    def test_doctor_healthy(self, runner, workspace):
        with patch("multi_agent.workspace.check_workspace_health", return_value=[]), \
             patch("multi_agent.workspace.get_workspace_stats", return_value={
                 "file_count": 5, "total_size_mb": 0.1, "largest_file": None,
             }):
            result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "正常" in result.output

    def test_doctor_with_issues(self, runner, workspace):
        with patch("multi_agent.workspace.check_workspace_health", return_value=["stale lock", "orphan file"]), \
             patch("multi_agent.workspace.get_workspace_stats", return_value={
                 "file_count": 10, "total_size_mb": 1.5, "largest_file": "big.json",
             }):
            result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "stale lock" in result.output
        assert "orphan file" in result.output
        assert "big.json" in result.output

    def test_doctor_fix_no_issues(self, runner, workspace):
        with patch("multi_agent.workspace.check_workspace_health", return_value=[]), \
             patch("multi_agent.workspace.get_workspace_stats", return_value={
                 "file_count": 5, "total_size_mb": 0.1, "largest_file": None,
             }), \
             patch("multi_agent.cli._auto_fix_runtime_consistency", return_value=[]):
            result = runner.invoke(main, ["doctor", "--fix"])
        assert result.exit_code == 0
        assert "未发现可自动修复" in result.output


# ── agents command (lines 284-285, 291) ──────────────────


class TestAgentsCommand:
    def test_no_agents(self, runner, workspace):
        with patch("multi_agent.router.load_agents", return_value=[]):
            result = runner.invoke(main, ["agents"])
        assert result.exit_code == 0
        assert "暂无" in result.output

    def test_agents_with_issues(self, runner, workspace):
        with patch("multi_agent.router.load_agents", return_value=[MagicMock(id="ws")]), \
             patch("multi_agent.router.check_agent_health", return_value=[
                 {"id": "ws", "status": "warning", "issues": ["no CLI binary"]},
             ]):
            result = runner.invoke(main, ["agents"])
        assert result.exit_code == 0
        assert "no CLI binary" in result.output


# ── list-skills command (lines 302-303, 308, 320-321) ────


class TestListSkillsCommand:
    def test_no_skills_dir(self, runner, workspace):
        # skills_dir doesn't have any skill subdirs
        result = runner.invoke(main, ["list-skills"])
        assert result.exit_code == 0

    def test_skip_dir_without_contract(self, runner, workspace):
        (workspace / "skills" / "no-contract").mkdir(parents=True)
        result = runner.invoke(main, ["list-skills"])
        assert result.exit_code == 0
        assert "no-contract" not in result.output

    def test_corrupt_contract_skipped(self, runner, workspace):
        skill_dir = workspace / "skills" / "bad-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "contract.yaml").write_text(":::\nbad: [yaml")
        result = runner.invoke(main, ["list-skills"])
        assert result.exit_code == 0


# ── export command (lines 343-347, 351-355, 362-363) ─────


class TestExportCommand:
    def test_export_corrupt_yaml(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        (td / "task-exp.yaml").write_text(":::\nbad: [yaml")
        hd = workspace / ".multi-agent" / "history"
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "task-exp.json").write_text(json.dumps([{"event": "test"}]))
        result = runner.invoke(main, ["export", "task-exp"])
        assert result.exit_code == 0
        out = json.loads(result.output)
        assert "_error" in out.get("config", {})

    def test_export_missing_history(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        (td / "task-nohist.yaml").write_text(yaml.dump({"task_id": "task-nohist", "status": "done"}))
        result = runner.invoke(main, ["export", "task-nohist"])
        assert result.exit_code == 0
        assert "未找到" in result.output

    def test_export_corrupt_history(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        (td / "task-badhist.yaml").write_text(yaml.dump({"task_id": "task-badhist"}))
        hd = workspace / ".multi-agent" / "history"
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "task-badhist.json").write_text("not json!")
        result = runner.invoke(main, ["export", "task-badhist"])
        assert result.exit_code == 0

    def test_export_markdown_format(self, runner, workspace):
        td = workspace / ".multi-agent" / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        (td / "task-md.yaml").write_text(yaml.dump({"task_id": "task-md", "status": "approved", "skill_id": "code-implement"}))
        hd = workspace / ".multi-agent" / "history"
        hd.mkdir(parents=True, exist_ok=True)
        (hd / "task-md.json").write_text(json.dumps([{"role": "builder", "action": "submit"}]))
        result = runner.invoke(main, ["export", "task-md", "--format", "markdown"])
        assert result.exit_code == 0
        assert "# Task:" in result.output
        assert "builder" in result.output


# ── trace command (lines 423-426) ────────────────────────


class TestTraceCommand:
    def test_trace_tree(self, runner, workspace):
        with patch("multi_agent.session.session_trace", return_value="# Trace\n\n1. start"):
            result = runner.invoke(main, ["trace", "--task-id", "task-tr"])
        assert result.exit_code == 0
        assert "Trace" in result.output

    def test_trace_mermaid(self, runner, workspace):
        with patch("multi_agent.session.session_trace", return_value="graph TD\n  A"):
            result = runner.invoke(main, ["trace", "--task-id", "task-tr", "--format", "mermaid"])
        assert result.exit_code == 0
        assert "graph TD" in result.output
