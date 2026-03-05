"""Tests for cli_queue.py — queue runner CLI commands."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from multi_agent.cli import main
from multi_agent.cli_queue import (
    _print_summary,
    extract_tasks_from_md,
    run_queue,
    run_single_queue_task,
)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def queue_md(tmp_path):
    """Create a minimal queue markdown file."""
    md = tmp_path / "tasks.md"
    md.write_text(
        "# Test Queue\n\n"
        "### 1. First task\n\n```\nImplement feature A\n```\n\n"
        "### 2. Second task\n\n```\nImplement feature B\n```\n\n"
        "### 3. Third task\n\n```\nImplement feature C\n```\n",
        encoding="utf-8",
    )
    return md


# ── extract_tasks_from_md ────────────────────────────────


class TestExtractTasks:
    def test_extracts_all_tasks(self, queue_md):
        tasks = extract_tasks_from_md(queue_md)
        assert len(tasks) == 3
        assert tasks[0] == (1, "First task", "Implement feature A")
        assert tasks[1] == (2, "Second task", "Implement feature B")

    def test_empty_file(self, tmp_path):
        md = tmp_path / "empty.md"
        md.write_text("# No tasks here\n\nJust text.\n")
        tasks = extract_tasks_from_md(md)
        assert tasks == []

    def test_malformed_blocks(self, tmp_path):
        md = tmp_path / "bad.md"
        md.write_text("### 1. Good\n\n```\nprompt\n```\n\n### 2. Bad (no code block)\n\ntext\n")
        tasks = extract_tasks_from_md(md)
        assert len(tasks) == 1
        assert tasks[0][0] == 1

    def test_single_newline_between_heading_and_code(self, tmp_path):
        """Regex should match even with only one newline after heading."""
        md = tmp_path / "single.md"
        md.write_text("### 1. Task\n```\nprompt text\n```\n")
        tasks = extract_tasks_from_md(md)
        assert len(tasks) == 1
        assert tasks[0] == (1, "Task", "prompt text")


# ── queue list command ───────────────────────────────────


class TestQueueListCommand:
    def test_list_tasks(self, runner, queue_md):
        result = runner.invoke(main, ["queue", "list", str(queue_md)])
        assert result.exit_code == 0
        assert "3 条任务" in result.output
        assert "First task" in result.output
        assert "Third task" in result.output

    def test_list_empty(self, runner, tmp_path):
        md = tmp_path / "empty.md"
        md.write_text("# Empty\n")
        result = runner.invoke(main, ["queue", "list", str(md)])
        assert result.exit_code == 0
        assert "未找到" in result.output


# ── queue run command ────────────────────────────────────


class TestQueueRunCommand:
    def test_dry_run(self, runner, queue_md):
        result = runner.invoke(main, ["queue", "run", str(queue_md), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output
        assert "3 条任务" in result.output

    def test_filter_start_end(self, runner, queue_md):
        result = runner.invoke(main, [
            "queue", "run", str(queue_md), "--dry-run", "--start", "2", "--end", "2",
        ])
        assert result.exit_code == 0
        assert "1 条任务" in result.output

    def test_filter_only(self, runner, queue_md):
        result = runner.invoke(main, [
            "queue", "run", str(queue_md), "--dry-run", "--only", "1,3",
        ])
        assert result.exit_code == 0
        assert "2 条任务" in result.output

    def test_run_executes(self, runner, queue_md, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        (tmp_path / ".multi-agent").mkdir()

        with patch("multi_agent.cli_queue.run_queue", return_value={
            "passed": [1], "failed": [], "details": [], "elapsed": "0h 0m 1s", "total": 1,
        }):
            result = runner.invoke(main, [
                "queue", "run", str(queue_md), "--only", "1",
            ])
        assert result.exit_code == 0
        assert "执行完成" in result.output
        # Assert results file was written
        report = tmp_path / ".multi-agent" / "queue-results.json"
        assert report.exists()
        data = json.loads(report.read_text())
        assert data["passed"] == [1]
        root_dir.cache_clear()

    def test_only_invalid_input(self, runner, queue_md):
        """--only with non-numeric values should show friendly error."""
        result = runner.invoke(main, [
            "queue", "run", str(queue_md), "--only", "a,b",
        ])
        assert result.exit_code == 0
        assert "格式错误" in result.output

    def test_only_with_trailing_comma(self, runner, queue_md):
        """--only '1,' should not crash (empty segment ignored)."""
        result = runner.invoke(main, [
            "queue", "run", str(queue_md), "--dry-run", "--only", "1,",
        ])
        assert result.exit_code == 0
        assert "1 条任务" in result.output


# ── queue status command ─────────────────────────────────


class TestQueueStatusCommand:
    def test_no_results(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        result = runner.invoke(main, ["queue", "status"])
        assert result.exit_code == 0
        assert "暂无" in result.output
        root_dir.cache_clear()

    def test_with_results(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        ws = tmp_path / ".multi-agent"
        ws.mkdir()
        (ws / "queue-results.json").write_text(json.dumps({
            "passed": [1, 2], "failed": [3], "elapsed": "0h 1m 30s",
        }))
        result = runner.invoke(main, ["queue", "status"])
        assert result.exit_code == 0
        assert "通过: 2" in result.output
        assert "失败: 1" in result.output
        root_dir.cache_clear()


# ── run_queue function ───────────────────────────────────


class TestRunQueueFunction:
    def test_collects_results(self):
        tasks = [(1, "T1", "p1"), (2, "T2", "p2")]
        with patch("multi_agent.cli_queue.run_single_queue_task") as mock_run:
            mock_run.side_effect = [
                {"num": 1, "title": "T1", "task_id": "task-queue-001", "status": "passed", "elapsed_sec": 1.0},
                {"num": 2, "title": "T2", "task_id": "task-queue-002", "status": "failed", "elapsed_sec": 2.0},
            ]
            with patch("time.sleep"):
                results = run_queue(tasks, "ws", "ag", 60, 0)
        assert results["passed"] == [1]
        assert results["failed"] == [2]
        assert results["total"] == 2


# ── run_single_queue_task ──────────────────────────────


class TestRunSingleQueueTask:
    def test_success(self):
        """Successful subprocess returns passed status."""
        mock_result = type("R", (), {"returncode": 0})()
        with patch("subprocess.run", return_value=mock_result):
            result = run_single_queue_task(1, "T1", "prompt", "ws", "ag", 60)
        assert result["status"] == "passed"
        assert result["task_id"] == "task-queue-001"
        assert result["num"] == 1
        assert "elapsed_sec" in result

    def test_failure(self):
        """Non-zero return code → failed status."""
        mock_result = type("R", (), {"returncode": 1})()
        with patch("subprocess.run", return_value=mock_result):
            result = run_single_queue_task(2, "T2", "prompt", "ws", "ag", 60)
        assert result["status"] == "failed"

    def test_timeout(self):
        """TimeoutExpired → timeout status, cancel attempt is safe."""
        import subprocess as sp
        with patch("subprocess.run", side_effect=sp.TimeoutExpired("ma", 60)):
            result = run_single_queue_task(3, "T3", "prompt", "ws", "ag", 60)
        assert result["status"] == "timeout"

    def test_keyboard_interrupt(self):
        """KeyboardInterrupt is re-raised after cancel attempt."""
        with patch("subprocess.run", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
            run_single_queue_task(4, "T4", "prompt", "ws", "ag", 60)


# ── _print_summary ────────────────────────────────────


class TestQueueRunCLIEdgeCases:
    """Cover CLI guard clauses: no tasks, filtered empty (lines 69-70, 84-85)."""

    def test_empty_queue_file(self, runner, tmp_path):
        md = tmp_path / "empty.md"
        md.write_text("# No tasks here\n\nJust text.\n")
        result = runner.invoke(main, ["queue", "run", str(md), "--dry-run"])
        assert "未找到任务" in result.output

    def test_filtered_to_empty(self, runner, queue_md):
        result = runner.invoke(main, ["queue", "run", str(queue_md), "--only", "99", "--dry-run"])
        assert "过滤后无任务" in result.output


class TestRunQueuePause:
    """Cover pause between tasks (line 210)."""

    def test_pause_between_tasks(self):
        mock_result = type("R", (), {"returncode": 0})()
        with patch("subprocess.run", return_value=mock_result), \
             patch("time.sleep") as mock_sleep:
            run_queue([(1, "T1", "p1"), (2, "T2", "p2")], "ws", "ag", 60, pause=1)
        # Should pause once (between task 1 and 2, not after the last)
        mock_sleep.assert_called_once_with(1)


class TestPrintSummary:
    def test_prints_passed_and_failed(self, capsys):
        _print_summary({"passed": [1, 2], "failed": [3], "elapsed": "0h 0m 5s"})
        out = capsys.readouterr().out
        assert "通过: 2" in out
        assert "失败: 1" in out
        assert "0h 0m 5s" in out
