"""Security tests — validate input sanitization, path traversal prevention, and boundary checks."""

from __future__ import annotations

import pytest

from multi_agent._utils import validate_agent_id, validate_task_id


class TestTaskIdValidation:
    """G1: task_id must be safe for file paths and SQL."""

    def test_valid_ids(self):
        for tid in ["task-001", "abc-def-ghi", "a12", "task-api-user-create"]:
            validate_task_id(tid)  # should not raise

    def test_path_traversal_rejected(self):
        for tid in ["../etc/passwd", "../../root", "foo/bar", "task/../escape"]:
            with pytest.raises(ValueError, match="invalid task_id"):
                validate_task_id(tid)

    def test_dot_dot_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("..")

    def test_tilde_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("~root")

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("")

    def test_too_short_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("ab")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("a" * 65)

    def test_uppercase_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("Task-001")

    def test_spaces_rejected(self):
        with pytest.raises(ValueError):
            validate_task_id("task 001")

    def test_special_chars_rejected(self):
        for tid in ["task;ls", "task&echo", "task|cat", "task$HOME"]:
            with pytest.raises(ValueError):
                validate_task_id(tid)


class TestAgentIdValidation:
    """G2: agent_id must be safe for file paths (inbox/outbox)."""

    def test_valid_ids(self):
        for aid in ["windsurf", "cursor", "codex", "kiro", "claude.3", "agent-1", "Agent_v2"]:
            validate_agent_id(aid)  # should not raise

    def test_path_traversal_rejected(self):
        for aid in ["../etc/passwd", "../../root", "foo/bar"]:
            with pytest.raises(ValueError, match="invalid agent_id"):
                validate_agent_id(aid)

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            validate_agent_id("")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError):
            validate_agent_id("a" * 65)

    def test_spaces_rejected(self):
        with pytest.raises(ValueError):
            validate_agent_id("agent 1")

    def test_shell_metachar_rejected(self):
        for aid in ["agent;ls", "agent|cat", "agent$x", "agent`cmd`"]:
            with pytest.raises(ValueError):
                validate_agent_id(aid)


class TestWorkspaceAgentValidation:
    """Verify workspace functions reject invalid agent_ids."""

    def test_write_inbox_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            from multi_agent.workspace import write_inbox
            with pytest.raises(ValueError, match="invalid agent_id"):
                write_inbox("../etc/passwd", "malicious content")
        finally:
            root_dir.cache_clear()

    def test_read_outbox_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            from multi_agent.workspace import read_outbox
            with pytest.raises(ValueError, match="invalid agent_id"):
                read_outbox("../../secrets")
        finally:
            root_dir.cache_clear()

    def test_write_outbox_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            from multi_agent.workspace import write_outbox
            with pytest.raises(ValueError, match="invalid agent_id"):
                write_outbox("../escape", {"data": "bad"})
        finally:
            root_dir.cache_clear()

    def test_clear_outbox_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            from multi_agent.workspace import clear_outbox
            with pytest.raises(ValueError, match="invalid agent_id"):
                clear_outbox("foo/bar")
        finally:
            root_dir.cache_clear()

    def test_clear_inbox_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            from multi_agent.workspace import clear_inbox
            with pytest.raises(ValueError, match="invalid agent_id"):
                clear_inbox("foo/bar")
        finally:
            root_dir.cache_clear()


class TestWorkspaceSymlinkProtection:
    """Verify workspace scans skip symlinks to prevent escape attacks."""

    def test_find_oversized_skips_symlinks(self, tmp_path):
        """Symlinked files should not be followed during oversized scan."""
        from multi_agent.workspace import _find_oversized_files

        # Create a real file and a symlink pointing outside workspace
        real = tmp_path / "real.txt"
        real.write_text("ok")
        outside = tmp_path.parent / "outside_secret.txt"
        outside.write_text("x" * 100)
        link = tmp_path / "escape.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("Cannot create symlinks on this filesystem")

        issues = _find_oversized_files(tmp_path)
        # Should not report the symlinked file as oversized (it's skipped)
        assert not any("escape.txt" in i and "Oversized" in i for i in issues)
        # Cleanup
        outside.unlink(missing_ok=True)

    def test_cleanup_old_files_skips_symlinks(self, tmp_path, monkeypatch):
        """cleanup_old_files must not follow symlinks outside workspace."""
        import time

        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            ws = tmp_path / ".multi-agent"
            tasks = ws / "tasks"
            tasks.mkdir(parents=True, exist_ok=True)
            # Create a file outside workspace
            outside = tmp_path / "precious.txt"
            outside.write_text("do not delete")
            # Create symlink inside workspace pointing outside
            link = tasks / "evil-link.yaml"
            try:
                link.symlink_to(outside)
            except OSError:
                pytest.skip("Cannot create symlinks")
            # Set old mtime on the symlink
            import os
            old_time = time.time() - 30 * 86400
            os.utime(str(link), (old_time, old_time), follow_symlinks=False)

            from multi_agent.workspace import cleanup_old_files
            cleanup_old_files(max_age_days=7)
            # The outside file must still exist
            assert outside.exists(), "Symlinked target was deleted!"
        finally:
            root_dir.cache_clear()
            outside.unlink(missing_ok=True)


class TestNotifyWebhookSSRF:
    """Verify webhook URL scheme validation prevents SSRF."""

    def test_file_scheme_rejected(self):
        from multi_agent.notify import _send_webhook
        assert _send_webhook("file:///etc/passwd", {"test": 1}) is False

    def test_ftp_scheme_rejected(self):
        from multi_agent.notify import _send_webhook
        assert _send_webhook("ftp://evil.com/data", {"test": 1}) is False

    def test_empty_url_rejected(self):
        from multi_agent.notify import _send_webhook
        assert _send_webhook("", {"test": 1}) is False

    def test_no_scheme_rejected(self):
        from multi_agent.notify import _send_webhook
        assert _send_webhook("evil.com/hook", {"test": 1}) is False


class TestNotifyAppleScriptEscape:
    """Verify AppleScript string escaping handles injection vectors."""

    def test_newline_stripped(self):
        from multi_agent.notify import _escape_applescript
        assert "\n" not in _escape_applescript("line1\nline2")
        assert "\r" not in _escape_applescript("line1\rline2")

    def test_quotes_escaped(self):
        from multi_agent.notify import _escape_applescript
        result = _escape_applescript('say "hello"')
        assert '\\"' in result

    def test_backslash_escaped(self):
        from multi_agent.notify import _escape_applescript
        result = _escape_applescript("path\\to\\file")
        assert "\\\\" in result


class TestSubtaskIdValidation:
    """Verify subtask_id validation prevents path traversal."""

    def test_valid_subtask_ids(self):
        from multi_agent.config import _validate_subtask_id
        for sid in ["task-abc-auth", "subtask.1", "A-B_C"]:
            _validate_subtask_id(sid)  # should not raise

    def test_path_traversal_rejected(self):
        from multi_agent.config import _validate_subtask_id
        for sid in ["../etc/passwd", "../../root", "foo/bar"]:
            with pytest.raises(ValueError, match="invalid subtask_id"):
                _validate_subtask_id(sid)

    def test_empty_rejected(self):
        from multi_agent.config import _validate_subtask_id
        with pytest.raises(ValueError):
            _validate_subtask_id("")

    def test_shell_metachar_rejected(self):
        from multi_agent.config import _validate_subtask_id
        for sid in ["sub;ls", "sub|cat", "sub$x"]:
            with pytest.raises(ValueError):
                _validate_subtask_id(sid)


class TestCheckpointSizeLimit:
    """Verify checkpoint load rejects oversized files."""

    def test_oversized_checkpoint_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            ckpt_dir = tmp_path / ".multi-agent" / "checkpoints"
            ckpt_dir.mkdir(parents=True, exist_ok=True)
            ckpt = ckpt_dir / "decompose-task-test123.json"
            # Write a file larger than 10 MB limit
            ckpt.write_text("x" * (11 * 1024 * 1024))
            from multi_agent.meta_graph import load_checkpoint
            result = load_checkpoint("task-test123")
            assert result is None
        finally:
            root_dir.cache_clear()


class TestLogTimingSanitization:
    """Verify log_timing sanitizes task_id in file paths."""

    def test_traversal_in_task_id_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            from multi_agent.graph_infra import log_timing
            # task_id with path traversal characters
            log_timing("../../etc/passwd", "build", 1.0, 2.0)
            logs_dir = tmp_path / ".multi-agent" / "logs"
            # File must be created inside logs_dir, not traverse out
            files = list(logs_dir.glob("timing-*.jsonl"))
            assert len(files) == 1
            # Key: slashes are removed, file stays within logs_dir
            assert "/" not in files[0].name
            # Verify the file is actually inside the logs dir
            assert files[0].parent == logs_dir
        finally:
            root_dir.cache_clear()


class TestGraphSnapshotSanitization:
    """Verify graph.py sanitizes task_id/node_name in snapshot paths."""

    def test_snapshot_path_no_traversal(self, tmp_path, monkeypatch):
        # Call the real save_state_snapshot with malicious task_ids
        # and verify files stay within the snapshots directory
        monkeypatch.setattr("multi_agent.config.workspace_dir", lambda: tmp_path)
        from multi_agent.graph_infra import save_state_snapshot
        for bad in ["../etc", "foo/bar", "x;rm -rf"]:
            save_state_snapshot(bad, "build", {"task_id": bad})
        snap_dir = tmp_path / "snapshots"
        for f in snap_dir.iterdir():
            assert f.parent == snap_dir  # no traversal escape
            assert "/" not in f.name
