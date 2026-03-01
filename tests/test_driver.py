"""Tests for agent driver — CLI spawn and file fallback."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from multi_agent import driver


class TestCanUseCli:
    def test_existing_binary(self):
        # 'echo' should exist on all systems
        assert driver.can_use_cli("echo hello world") is True

    def test_missing_binary(self):
        assert driver.can_use_cli("nonexistent_binary_xyz_99 --flag") is False

    def test_empty_command(self):
        assert driver.can_use_cli("") is False
        assert driver.can_use_cli("   ") is False


class TestGetAgentDriver:
    def test_cli_agent(self):
        from multi_agent.schema import AgentProfile
        agents = [
            AgentProfile(id="claude", driver="cli", command="claude -p '{task_file}'"),
            AgentProfile(id="windsurf", driver="file"),
        ]
        with patch("multi_agent.router.load_agents", return_value=agents):
            drv = driver.get_agent_driver("claude")
            assert drv["driver"] == "cli"
            assert "claude" in drv["command"]

    def test_file_agent(self):
        from multi_agent.schema import AgentProfile
        agents = [AgentProfile(id="windsurf", driver="file")]
        with patch("multi_agent.router.load_agents", return_value=agents):
            drv = driver.get_agent_driver("windsurf")
            assert drv["driver"] == "file"
            assert drv["command"] == ""

    def test_unknown_agent_defaults_to_file(self):
        with patch("multi_agent.router.load_agents", return_value=[]):
            drv = driver.get_agent_driver("unknown")
            assert drv["driver"] == "file"

    def test_missing_driver_field_defaults_to_file(self):
        from multi_agent.schema import AgentProfile
        agents = [AgentProfile(id="old_agent")]
        with patch("multi_agent.router.load_agents", return_value=agents):
            drv = driver.get_agent_driver("old_agent")
            assert drv["driver"] == "file"


class TestTryExtractJson:
    def test_extracts_from_code_block(self, tmp_path):
        text = 'Here is the result:\n```json\n{"status": "completed", "summary": "done"}\n```\nDone.'
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert outbox.exists()
        data = json.loads(outbox.read_text())
        assert data["status"] == "completed"

    def test_extracts_raw_json(self, tmp_path):
        text = '{"status": "completed", "summary": "raw"}'
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert outbox.exists()
        data = json.loads(outbox.read_text())
        assert data["summary"] == "raw"

    def test_ignores_non_json(self, tmp_path):
        text = "This is not JSON at all"
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert not outbox.exists()

    def test_ignores_non_dict_json(self, tmp_path):
        text = '["not", "a", "dict"]'
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert not outbox.exists()


class TestWriteError:
    def test_writes_error_json(self, tmp_path):
        outbox = str(tmp_path / "outbox" / "builder.json")
        driver._write_error(outbox, "timeout")
        data = json.loads(Path(outbox).read_text())
        assert data["status"] == "error"
        assert "timeout" in data["summary"]


class TestSpawnCliAgent:
    def test_spawns_echo_command(self, tmp_path):
        """Test that spawn_cli_agent runs a command and writes outbox."""
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()
        outbox_file = outbox_dir / "builder.json"

        # Command that writes JSON directly to {outbox_file}
        cmd = 'echo \'{{"status": "completed", "summary": "test"}}\' > {outbox_file}'

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_dir):
            t = driver.spawn_cli_agent("test", "builder", cmd, str(tmp_path))
            t.join(timeout=10)

        assert outbox_file.exists()
        data = json.loads(outbox_file.read_text())
        assert data["status"] == "completed"

    def test_nonzero_exit_writes_error(self, tmp_path):
        """Test that non-zero exit with no stdout produces an error in outbox."""
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()

        # Command that exits with error and no JSON output
        cmd = "exit 1"

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_dir):
            t = driver.spawn_cli_agent("test", "builder", cmd, str(tmp_path))
            t.join(timeout=10)

        outbox_file = outbox_dir / "builder.json"
        assert outbox_file.exists()
        data = json.loads(outbox_file.read_text())
        assert data["status"] == "error"
        assert "exited with code" in data["summary"]

    def test_zero_exit_no_json_writes_error(self, tmp_path):
        """Test that zero exit with no parseable JSON still writes error."""
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()

        cmd = "echo 'not json at all'"

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_dir):
            t = driver.spawn_cli_agent("test", "builder", cmd, str(tmp_path))
            t.join(timeout=10)

        outbox_file = outbox_dir / "builder.json"
        assert outbox_file.exists()
        data = json.loads(outbox_file.read_text())
        assert data["status"] == "error"
        assert "no parseable JSON" in data["summary"]

    def test_timeout_writes_error(self, tmp_path):
        """Test that timeout produces an error in outbox."""
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()

        import subprocess as real_subprocess

        mock_proc = MagicMock()
        mock_proc.stderr = iter([])
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.read.return_value = ""
        mock_proc.wait.side_effect = real_subprocess.TimeoutExpired("sleep 999", 600)
        mock_proc.kill = MagicMock()

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_dir), \
             patch("multi_agent.driver.subprocess.Popen", return_value=mock_proc):
            t = driver.spawn_cli_agent("test", "builder", "sleep 999", str(tmp_path))
            t.join(timeout=10)

        outbox_file = outbox_dir / "builder.json"
        assert outbox_file.exists()
        data = json.loads(outbox_file.read_text())
        assert data["status"] == "error"
        assert "timed out" in data["summary"]
        mock_proc.kill.assert_called_once()


class TestStreamStderr:
    """Task 9: Verify stderr real-time logging."""

    def test_stream_stderr_collects_lines(self):
        mock_proc = MagicMock()
        mock_proc.stderr = iter(["line1\n", "line2\n", "warning: something\n"])
        result = driver._stream_stderr(mock_proc, "claude", "builder")
        assert "line1" in result
        assert "line2" in result
        assert "warning: something" in result

    def test_stream_stderr_empty(self):
        mock_proc = MagicMock()
        mock_proc.stderr = iter([])
        result = driver._stream_stderr(mock_proc, "claude", "builder")
        assert result == ""

    def test_stream_stderr_none(self):
        mock_proc = MagicMock()
        mock_proc.stderr = None
        result = driver._stream_stderr(mock_proc, "claude", "builder")
        assert result == ""

    def test_stderr_logged(self, caplog):
        """Verify stderr lines are logged at INFO level."""
        import logging
        mock_proc = MagicMock()
        mock_proc.stderr = iter(["err1\n"])
        with caplog.at_level(logging.INFO, logger="multi_agent.driver"):
            driver._stream_stderr(mock_proc, "test-agent", "builder")
        assert any("err1" in r.message for r in caplog.records)


class TestConcurrencyProtection:
    """Task 10: Verify CLI agent concurrency lock."""

    def test_duplicate_spawn_returns_existing(self, tmp_path):
        """Spawning same agent+role while alive should return existing thread."""
        outbox_d = tmp_path / "outbox"
        outbox_d.mkdir()

        # Create a mock thread that appears alive
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_d):
            driver._active_agents["dup-agent:builder"] = fake_thread
            try:
                result = driver.spawn_cli_agent("dup-agent", "builder", "echo hi", str(tmp_path))
                assert result is fake_thread
            finally:
                driver._active_agents.pop("dup-agent:builder", None)

    def test_finished_agent_can_respawn(self, tmp_path):
        """After thread finishes, same agent+role can be spawned again."""
        outbox_d = tmp_path / "outbox"
        outbox_d.mkdir()

        # Create a mock thread that appears dead
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = False

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_d):
            driver._active_agents["done-agent:builder"] = fake_thread
            try:
                t = driver.spawn_cli_agent("done-agent", "builder", "echo done", str(tmp_path))
                t.join(timeout=10)
            finally:
                driver._active_agents.pop("done-agent:builder", None)

    def test_thread_cleanup_on_finish(self, tmp_path):
        """Thread should be removed from _active_agents after finishing."""
        outbox_d = tmp_path / "outbox"
        outbox_d.mkdir()
        outbox_file = outbox_d / "builder.json"

        cmd = 'echo \'{{"status": "completed", "summary": "ok"}}\' > {outbox_file}'

        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_d):
            t = driver.spawn_cli_agent("clean-agent", "builder", cmd, str(tmp_path))
            t.join(timeout=10)

        # After thread finishes, it should be cleaned up
        assert "clean-agent:builder" not in driver._active_agents


class TestDriverBoundary:
    """Task 44: Driver boundary tests."""

    def test_try_extract_json_markdown_wrapped(self, tmp_path):
        text = 'Result:\n```json\n{"status": "completed", "summary": "wrapped"}\n```'
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert outbox.exists()
        data = json.loads(outbox.read_text())
        assert data["summary"] == "wrapped"

    def test_try_extract_json_no_json(self, tmp_path):
        text = "No JSON here at all, just plain text"
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert not outbox.exists()

    def test_try_extract_json_multiple_blocks(self, tmp_path):
        text = '```json\n{"status": "first"}\n```\n```json\n{"status": "second"}\n```'
        outbox = tmp_path / "builder.json"
        driver._try_extract_json(text, outbox)
        assert outbox.exists()
        data = json.loads(outbox.read_text())
        assert data["status"] == "first"

    def test_write_error_creates_parent_dir(self, tmp_path):
        outbox = str(tmp_path / "deep" / "dir" / "builder.json")
        driver._write_error(outbox, "test error")
        data = json.loads(Path(outbox).read_text())
        assert data["status"] == "error"

    def test_command_template_placeholders(self, tmp_path):
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()
        cmd = "echo {task_file} {outbox_file}"
        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path), \
             patch("multi_agent.driver.outbox_dir", return_value=outbox_dir):
            t = driver.spawn_cli_agent("tmpl", "builder", cmd, str(tmp_path))
            t.join(timeout=10)

    def test_can_use_cli_with_path_binary(self):
        assert driver.can_use_cli("/usr/bin/env echo test") is True

    def test_get_agent_driver_unknown_returns_file(self):
        with patch("multi_agent.router.load_agents", return_value=[]):
            drv = driver.get_agent_driver("totally_unknown")
        assert drv["driver"] == "file"
        assert drv["command"] == ""

    def test_get_agent_driver_cli_with_command(self):
        from multi_agent.schema import AgentProfile
        agents = [AgentProfile(id="claude", driver="cli", command="claude -p '{task_file}'")]
        with patch("multi_agent.router.load_agents", return_value=agents):
            drv = driver.get_agent_driver("claude")
        assert drv["driver"] == "cli"
        assert "{task_file}" in drv["command"]


class TestGetLatestLog:
    """Task 9: get_latest_log tests."""

    def test_no_logs_returns_none(self, tmp_path):
        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path):
            from multi_agent.driver import get_latest_log
            assert get_latest_log("claude") is None

    def test_returns_latest_log(self, tmp_path):
        import time
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        old_log = logs_dir / "claude-builder-1000.log"
        old_log.write_text("old")
        time.sleep(0.05)
        new_log = logs_dir / "claude-builder-2000.log"
        new_log.write_text("new")
        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path):
            from multi_agent.driver import get_latest_log
            result = get_latest_log("claude")
            assert result is not None
            assert result.name == "claude-builder-2000.log"

    def test_no_matching_agent(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "other-builder-1000.log").write_text("x")
        with patch("multi_agent.driver.workspace_dir", return_value=tmp_path):
            from multi_agent.driver import get_latest_log
            assert get_latest_log("claude") is None


class TestClassifyStderr:
    """Task 64: stderr classification tests."""

    def test_error_keywords(self):
        from multi_agent.driver import classify_stderr
        assert classify_stderr("Error: file not found") == "error"
        assert classify_stderr("FATAL: disk full") == "error"
        assert classify_stderr("Traceback (most recent call last):") == "error"

    def test_warning_keywords(self):
        from multi_agent.driver import classify_stderr
        assert classify_stderr("Warning: deprecated API") == "warning"
        assert classify_stderr("DeprecationWarning: use new_func") == "warning"

    def test_info_default(self):
        from multi_agent.driver import classify_stderr
        assert classify_stderr("Processing file...") == "info"
        assert classify_stderr("") == "info"
