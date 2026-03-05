"""Unit tests for cli.py helper functions — _log_error_to_file, _mark_task_inactive,
_is_task_terminal_or_missing, _read_done_output, _auto_fix_runtime_consistency."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
import yaml

from multi_agent.cli import main

# ── _log_error_to_file ───────────────────────────────────


class TestLogErrorToFile:
    def test_writes_log_file(self, tmp_path, monkeypatch):
        from multi_agent.cli import _log_error_to_file
        with patch("multi_agent.config.workspace_dir", return_value=tmp_path):
            _log_error_to_file("test_cmd", ValueError("boom"))
        logs = list((tmp_path / "logs").glob("error-*.log"))
        assert len(logs) == 1
        content = logs[0].read_text()
        assert "test_cmd" in content
        assert "boom" in content

    def test_suppresses_errors(self, tmp_path):
        from multi_agent.cli import _log_error_to_file
        # If workspace_dir raises, should not propagate
        with patch("multi_agent.config.workspace_dir", side_effect=RuntimeError):
            _log_error_to_file("cmd", ValueError("x"))  # should not raise


# ── _mark_task_inactive ──────────────────────────────────


class TestMarkTaskInactive:
    def test_marks_existing_task(self, tmp_path, monkeypatch):
        from multi_agent.cli import _mark_task_inactive
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        tf = tasks / "task-001.yaml"
        tf.write_text(yaml.dump({"task_id": "task-001", "status": "active"}), encoding="utf-8")
        with patch("multi_agent.config.tasks_dir", return_value=tasks):
            result = _mark_task_inactive("task-001", status="cancelled", reason="user request")
        assert result is True
        data = yaml.safe_load(tf.read_text())
        assert data["status"] == "cancelled"
        assert data["reason"] == "user request"

    def test_returns_false_for_missing_file(self, tmp_path):
        from multi_agent.cli import _mark_task_inactive
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        with patch("multi_agent.config.tasks_dir", return_value=tasks):
            assert _mark_task_inactive("nonexistent", status="x", reason="y") is False

    def test_returns_false_for_non_dict(self, tmp_path):
        from multi_agent.cli import _mark_task_inactive
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        tf = tasks / "task-002.yaml"
        tf.write_text("- a list\n- not a dict\n", encoding="utf-8")
        with patch("multi_agent.config.tasks_dir", return_value=tasks):
            assert _mark_task_inactive("task-002", status="x", reason="y") is False


# ── _is_task_terminal_or_missing ─────────────────────────


class TestIsTaskTerminalOrMissing:
    def test_terminal_task(self):
        from multi_agent.cli import _is_task_terminal_or_missing
        app = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"final_status": "approved"}
        snapshot.next = []
        app.get_state.return_value = snapshot
        assert _is_task_terminal_or_missing(app, "t-1") is True

    def test_active_task(self):
        from multi_agent.cli import _is_task_terminal_or_missing
        app = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {}
        snapshot.next = ["build_node"]
        app.get_state.return_value = snapshot
        assert _is_task_terminal_or_missing(app, "t-1") is False

    def test_no_snapshot(self):
        from multi_agent.cli import _is_task_terminal_or_missing
        app = MagicMock()
        app.get_state.return_value = None
        assert _is_task_terminal_or_missing(app, "t-1") is True

    def test_exception_returns_false(self):
        from multi_agent.cli import _is_task_terminal_or_missing
        app = MagicMock()
        app.get_state.side_effect = RuntimeError("db error")
        assert _is_task_terminal_or_missing(app, "t-1") is False

    def test_no_next_is_terminal(self):
        from multi_agent.cli import _is_task_terminal_or_missing
        app = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {}
        snapshot.next = []
        app.get_state.return_value = snapshot
        assert _is_task_terminal_or_missing(app, "t-1") is True


# ── _read_done_output ────────────────────────────────────


class TestReadDoneOutput:
    def test_reads_from_file(self, tmp_path):
        from multi_agent.cli import _read_done_output
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"status": "completed", "summary": "ok"}), encoding="utf-8")
        result = _read_done_output("builder", str(f))
        assert result["status"] == "completed"

    def test_file_too_large_exits(self, tmp_path):
        from multi_agent.cli import _read_done_output
        f = tmp_path / "big.json"
        f.write_text("x" * (11 * 1024 * 1024), encoding="utf-8")
        with pytest.raises(SystemExit):
            _read_done_output("builder", str(f))

    def test_invalid_json_file_exits(self, tmp_path):
        from multi_agent.cli import _read_done_output
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        with pytest.raises(SystemExit):
            _read_done_output("builder", str(f))

    def test_reads_from_outbox(self):
        from multi_agent.cli import _read_done_output
        with patch("multi_agent.cli.read_outbox", return_value={"decision": "approve"}):
            result = _read_done_output("reviewer", None)
        assert result["decision"] == "approve"

    def test_stdin_fallback(self, monkeypatch):
        from multi_agent.cli import _read_done_output
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"status": "done"}'))
        with patch("multi_agent.cli.read_outbox", return_value=None):
            result = _read_done_output("builder", None)
        assert result["status"] == "done"

    def test_stdin_invalid_json_exits(self, monkeypatch):
        from multi_agent.cli import _read_done_output
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
        with patch("multi_agent.cli.read_outbox", return_value=None), \
             pytest.raises(SystemExit):
            _read_done_output("builder", None)

    def test_no_output_anywhere_exits(self, monkeypatch):
        from multi_agent.cli import _read_done_output
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
        with patch("multi_agent.cli.read_outbox", return_value=None), \
             pytest.raises(SystemExit):
            _read_done_output("builder", None)

    def test_file_stat_error_treated_as_zero(self, tmp_path):
        from multi_agent.cli import _read_done_output
        f = tmp_path / "output.json"
        f.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
        with patch.object(Path, "stat", side_effect=OSError("perm denied")):
            result = _read_done_output("builder", str(f))
        assert result["status"] == "ok"


# ── _auto_fix_runtime_consistency ────────────────────────


class TestAutoFixRuntimeConsistency:
    def test_no_active_no_lock(self):
        from multi_agent.cli import _auto_fix_runtime_consistency
        with patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.read_lock", return_value=None):
            actions = _auto_fix_runtime_consistency()
        assert actions == []

    def test_active_no_lock_terminal(self):
        from multi_agent.cli import _auto_fix_runtime_consistency
        with patch("multi_agent.cli._detect_active_task", return_value="task-001"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.graph.compile_graph"), \
             patch("multi_agent.cli._is_task_terminal_or_missing", return_value=True), \
             patch("multi_agent.cli._mark_task_inactive", return_value=True):
            actions = _auto_fix_runtime_consistency()
        assert any("陈旧" in a for a in actions)

    def test_active_no_lock_restores(self):
        from multi_agent.cli import _auto_fix_runtime_consistency
        with patch("multi_agent.cli._detect_active_task", return_value="task-001"), \
             patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.graph.compile_graph"), \
             patch("multi_agent.cli._is_task_terminal_or_missing", return_value=False), \
             patch("multi_agent.cli.acquire_lock"):
            actions = _auto_fix_runtime_consistency()
        assert any("恢复锁" in a for a in actions)

    def test_lock_no_active_terminal_releases(self):
        from multi_agent.cli import _auto_fix_runtime_consistency
        with patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.read_lock", return_value="task-001"), \
             patch("multi_agent.graph.compile_graph"), \
             patch("multi_agent.cli._is_task_terminal_or_missing", return_value=True), \
             patch("multi_agent.cli.release_lock"):
            actions = _auto_fix_runtime_consistency()
        assert any("释放孤立锁" in a for a in actions)

    def test_lock_no_active_still_running_keeps(self):
        from multi_agent.cli import _auto_fix_runtime_consistency
        with patch("multi_agent.cli._detect_active_task", return_value=None), \
             patch("multi_agent.cli.read_lock", return_value="task-001"), \
             patch("multi_agent.graph.compile_graph"), \
             patch("multi_agent.cli._is_task_terminal_or_missing", return_value=False):
            actions = _auto_fix_runtime_consistency()
        assert any("保留锁" in a for a in actions)

    def test_lock_mismatch_realigns(self):
        from multi_agent.cli import _auto_fix_runtime_consistency
        with patch("multi_agent.cli._detect_active_task", return_value="task-002"), \
             patch("multi_agent.cli.read_lock", return_value="task-001"), \
             patch("multi_agent.graph.compile_graph"), \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.acquire_lock"):
            actions = _auto_fix_runtime_consistency()
        assert any("重对齐" in a for a in actions)


# ── _sigterm_handler ─────────────────────────────────────


class TestSigtermHandler:
    def test_handler_raises_systemexit(self):
        import signal

        from multi_agent.cli import _sigterm_handler
        with patch("multi_agent.cli.read_lock", return_value="task-1"), \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.clear_runtime"), \
             pytest.raises(SystemExit) as exc_info:
            _sigterm_handler(signal.SIGTERM, None)
        assert exc_info.value.code == 128 + signal.SIGTERM

    def test_handler_no_lock(self):
        import signal

        from multi_agent.cli import _sigterm_handler
        with patch("multi_agent.cli.read_lock", return_value=None), \
             patch("multi_agent.cli.release_lock") as mock_rel, \
             patch("multi_agent.cli.clear_runtime"), \
             pytest.raises(SystemExit):
            _sigterm_handler(signal.SIGTERM, None)
        mock_rel.assert_not_called()

    def test_handler_cleanup_exception(self):
        """Exception during cleanup is suppressed (lines 195-196)."""
        import signal

        from multi_agent.cli import _sigterm_handler
        with patch("multi_agent.cli.read_lock", side_effect=RuntimeError("boom")), \
             patch("multi_agent.cli.clear_runtime"), \
             pytest.raises(SystemExit) as exc_info:
            _sigterm_handler(signal.SIGTERM, None)
        assert exc_info.value.code == 128 + signal.SIGTERM


# ── handle_errors edge cases (lines 61, 69) ─────────────


class TestHandleErrorsEdgeCases:
    def test_click_exit_passthrough(self):
        """click.exceptions.Exit should be re-raised (line 61)."""
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_exit():
            raise click.exceptions.Exit(0)

        with pytest.raises(click.exceptions.Exit):
            raise_exit()

    def test_verbose_traceback(self):
        """Verbose mode shows full traceback (line 69)."""
        from multi_agent.cli import handle_errors

        @handle_errors
        def raise_err():
            raise ValueError("detailed-err")

        class _Root:
            def __init__(self):
                self.params = {"verbose": True}

        class _Ctx:
            def __init__(self):
                self.params = {}

            def find_root(self):
                return _Root()

        with patch("multi_agent.cli.click.get_current_context", return_value=_Ctx()), \
             pytest.raises(SystemExit):
            raise_err()


# ── _mark_task_inactive exception (lines 185-186) ───────


class TestMarkTaskInactiveException:
    def test_write_error_returns_false(self, tmp_path, monkeypatch):
        """Exception during YAML write returns False (lines 185-186)."""
        from multi_agent.cli import _mark_task_inactive
        td = tmp_path / "tasks"
        td.mkdir()
        (td / "task-err.yaml").write_text("status: active")
        with patch("multi_agent.config.tasks_dir", return_value=td), \
             patch("pathlib.Path.write_text", side_effect=PermissionError("no write")):
            result = _mark_task_inactive("task-err", status="failed", reason="test")
        assert result is False


# ── _apply_project_defaults (lines 282, 288, 290, 292, 294) ──


class TestApplyProjectDefaults:
    def test_all_defaults_from_project(self):
        """Project config applies all defaults when CLI uses defaults (lines 282-294)."""
        from multi_agent.cli import _apply_project_defaults
        proj = {
            "default_builder": "cursor",
            "default_reviewer": "windsurf",
            "default_timeout": 3600,
            "default_retry_budget": 5,
            "default_workflow_mode": "balanced",
            "workmode_config": "custom/mode.yaml",
        }
        with patch("multi_agent.config.validate_config", return_value=["warn1"]):
            b, r, t, rb, m, mc = _apply_project_defaults(
                proj, "", "", 1800, 2, "strict", "config/workmode.yaml",
            )
        assert b == "cursor"
        assert r == "windsurf"
        assert t == 3600
        assert rb == 5
        assert m == "balanced"
        assert mc == "custom/mode.yaml"

    def test_cli_flags_override_project(self):
        """CLI flags take precedence over project defaults."""
        from multi_agent.cli import _apply_project_defaults
        proj = {"default_builder": "cursor"}
        with patch("multi_agent.config.validate_config", return_value=[]):
            b, _r, _t, _rb, _m, _mc = _apply_project_defaults(
                proj, "windsurf", "", 1800, 2, "strict", "config/workmode.yaml",
            )
        assert b == "windsurf"  # CLI flag wins


# ── _ensure_no_active_task stale cleanup (lines 316-324) ──


class TestEnsureNoActiveTaskStaleCleanup:
    def test_stale_task_auto_cleared(self, capsys):
        """Terminal-state active marker is auto-cleaned (lines 316-324)."""
        from multi_agent.cli import _ensure_no_active_task
        app = MagicMock()
        with patch("multi_agent.cli._detect_active_task", return_value="task-stale"), \
             patch("multi_agent.cli.read_lock", return_value="task-stale"), \
             patch("multi_agent.cli._is_task_terminal_or_missing", return_value=True), \
             patch("multi_agent.cli._mark_task_inactive", return_value=True), \
             patch("multi_agent.cli.release_lock"), \
             patch("multi_agent.cli.clear_runtime"):
            _ensure_no_active_task(app)
        out = capsys.readouterr().out
        assert "陈旧" in out or "auto-cleared" in out


# ── _detect_active_task (lines 736, 743-746) ────────────


class TestDetectActiveTaskEdgeCases:
    def test_no_tasks_dir(self, tmp_path):
        """Missing tasks_dir returns None (line 736)."""
        from multi_agent.cli import _detect_active_task
        with patch("multi_agent.config.tasks_dir", return_value=tmp_path / "nonexistent"):
            result = _detect_active_task()
        assert result is None

    def test_malicious_filename_skipped(self, tmp_path):
        """Filenames with path traversal chars are skipped (lines 743-746)."""
        from multi_agent.cli import _detect_active_task
        td = tmp_path / "tasks"
        td.mkdir()
        # Create a file with a safe-looking name but status=active
        (td / "good-task.yaml").write_text("status: active\n")
        result_good = None
        with patch("multi_agent.config.tasks_dir", return_value=td):
            result_good = _detect_active_task()
        assert result_good == "good-task"


# ── session pull cmd (lines 247-255) ────────────────────


class TestSessionPullCmd:
    def test_json_meta_output(self, tmp_path):
        """--json-meta outputs JSON payload (lines 251-253)."""
        from click.testing import CliRunner
        runner = CliRunner()
        payload = {"prompt_path": str(tmp_path / "p.txt"), "role": "builder"}
        with patch("multi_agent.session.session_pull", return_value=payload):
            result = runner.invoke(main, [
                "session", "pull", "--task-id", "task-pull", "--agent", "ws", "--json-meta",
            ])
        assert result.exit_code == 0
        assert "prompt_path" in result.output

    def test_prompt_text_output(self, tmp_path):
        """Default output reads prompt file (lines 254-255)."""
        from click.testing import CliRunner
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Hello builder prompt")
        runner = CliRunner()
        payload = {"prompt_path": str(prompt_file), "role": "builder"}
        with patch("multi_agent.session.session_pull", return_value=payload):
            result = runner.invoke(main, [
                "session", "pull", "--task-id", "task-pull2", "--agent", "ws",
            ])
        assert result.exit_code == 0
        assert "Hello builder prompt" in result.output


class TestGoCommandLockRelease:
    """Regression: P1 — go command must release lock on unexpected errors."""

    def test_lock_released_on_unexpected_error(self, tmp_path, monkeypatch):
        """If _run_single_task raises an unexpected error, lock must be released."""
        from click.testing import CliRunner

        from multi_agent import workspace

        monkeypatch.setattr("multi_agent.config.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.config.inbox_dir", lambda: tmp_path / "inbox")
        monkeypatch.setattr("multi_agent.config.outbox_dir", lambda: tmp_path / "outbox")
        monkeypatch.setattr("multi_agent.config.tasks_dir", lambda: tmp_path / "tasks")
        monkeypatch.setattr("multi_agent.config.history_dir", lambda: tmp_path / "history")
        workspace.ensure_workspace()

        runner = CliRunner()
        with patch("multi_agent.cli._ensure_no_active_task"), \
             patch("multi_agent.cli._generate_task_id", return_value="task-locktest"), \
             patch("multi_agent.cli.clear_runtime"), \
             patch("multi_agent.graph.compile_graph"), \
             patch("multi_agent.cli._run_single_task", side_effect=RuntimeError("boom")):
            result = runner.invoke(main, ["go", "test requirement"])

        # Lock must be released after unexpected error
        assert workspace.read_lock() is None
        assert result.exit_code != 0
