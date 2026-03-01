"""Tests for ma history command (Task 15)."""

import yaml
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from multi_agent.cli import main


class TestHistory:

    def _setup_tasks(self, tmp_path):
        td = tmp_path / "tasks"
        td.mkdir(parents=True, exist_ok=True)
        return td

    def test_empty_history(self, tmp_path):
        td = self._setup_tasks(tmp_path)
        with patch("multi_agent.config.tasks_dir", return_value=td):
            runner = CliRunner()
            result = runner.invoke(main, ["history"])
        assert "暂无历史任务记录" in result.output

    def test_shows_tasks(self, tmp_path):
        td = self._setup_tasks(tmp_path)
        (td / "task-abc.yaml").write_text(yaml.dump({"task_id": "task-abc", "status": "approved"}))
        (td / "task-def.yaml").write_text(yaml.dump({"task_id": "task-def", "status": "failed"}))
        with patch("multi_agent.config.tasks_dir", return_value=td):
            runner = CliRunner()
            result = runner.invoke(main, ["history"])
        assert "task-abc" in result.output
        assert "task-def" in result.output
        assert "approved" in result.output
        assert "failed" in result.output

    def test_filter_status(self, tmp_path):
        td = self._setup_tasks(tmp_path)
        (td / "task-a.yaml").write_text(yaml.dump({"task_id": "task-a", "status": "approved"}))
        (td / "task-b.yaml").write_text(yaml.dump({"task_id": "task-b", "status": "failed"}))
        with patch("multi_agent.config.tasks_dir", return_value=td):
            runner = CliRunner()
            result = runner.invoke(main, ["history", "--status", "failed"])
        assert "task-b" in result.output
        assert "task-a" not in result.output

    def test_limit(self, tmp_path):
        td = self._setup_tasks(tmp_path)
        for i in range(5):
            (td / f"task-{i:03d}.yaml").write_text(yaml.dump({"task_id": f"task-{i:03d}", "status": "approved"}))
        with patch("multi_agent.config.tasks_dir", return_value=td):
            runner = CliRunner()
            result = runner.invoke(main, ["history", "--limit", "2"])
        count = result.output.count("task-")
        assert count == 2

    def test_filter_no_match(self, tmp_path):
        td = self._setup_tasks(tmp_path)
        (td / "task-a.yaml").write_text(yaml.dump({"task_id": "task-a", "status": "approved"}))
        with patch("multi_agent.config.tasks_dir", return_value=td):
            runner = CliRunner()
            result = runner.invoke(main, ["history", "--status", "cancelled"])
        assert "暂无 status=cancelled" in result.output

    def test_nonexistent_tasks_dir(self, tmp_path):
        td = tmp_path / "nonexistent" / "tasks"
        with patch("multi_agent.config.tasks_dir", return_value=td):
            runner = CliRunner()
            result = runner.invoke(main, ["history"])
        assert "暂无历史任务记录" in result.output
