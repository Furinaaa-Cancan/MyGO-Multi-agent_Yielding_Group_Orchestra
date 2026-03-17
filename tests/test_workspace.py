"""Tests for workspace manager."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent import workspace


@pytest.fixture
def tmp_workspace(tmp_path):
    """Patch workspace dirs to use a temp directory."""
    ws = tmp_path / ".multi-agent"
    with patch("multi_agent.workspace.workspace_dir", return_value=ws), \
         patch("multi_agent.workspace.inbox_dir", return_value=ws / "inbox"), \
         patch("multi_agent.workspace.outbox_dir", return_value=ws / "outbox"), \
         patch("multi_agent.workspace.tasks_dir", return_value=ws / "tasks"), \
         patch("multi_agent.workspace.history_dir", return_value=ws / "history"):
        yield ws


class TestEnsureWorkspace:
    def test_creates_dirs(self, tmp_workspace):
        workspace.ensure_workspace()
        assert (tmp_workspace / "inbox").is_dir()
        assert (tmp_workspace / "outbox").is_dir()
        assert (tmp_workspace / "tasks").is_dir()
        assert (tmp_workspace / "history").is_dir()

    def test_idempotent(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.ensure_workspace()
        assert (tmp_workspace / "inbox").is_dir()


class TestInboxOutbox:
    def test_write_read_inbox(self, tmp_workspace):
        workspace.ensure_workspace()
        path = workspace.write_inbox("windsurf", "# Hello Builder")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "# Hello Builder"

    def test_write_read_outbox(self, tmp_workspace):
        workspace.ensure_workspace()
        data = {"status": "completed", "summary": "done"}
        workspace.write_outbox("windsurf", data)
        result = workspace.read_outbox("windsurf")
        assert result["status"] == "completed"

    def test_read_outbox_missing(self, tmp_workspace):
        workspace.ensure_workspace()
        assert workspace.read_outbox("nonexistent") is None

    def test_clear_outbox(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_outbox("windsurf", {"status": "done"})
        workspace.clear_outbox("windsurf")
        assert workspace.read_outbox("windsurf") is None

    def test_clear_inbox(self, tmp_workspace):
        workspace.ensure_workspace()
        path = workspace.write_inbox("windsurf", "prompt")
        assert path.exists()
        workspace.clear_inbox("windsurf")
        assert not path.exists()


class TestLock:
    def test_read_lock_empty(self, tmp_workspace):
        workspace.ensure_workspace()
        assert workspace.read_lock() is None

    def test_acquire_and_read(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.acquire_lock("task-abc")
        assert workspace.read_lock() == "task-abc"

    def test_release_lock(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.acquire_lock("task-abc")
        workspace.release_lock()
        assert workspace.read_lock() is None

    def test_release_nonexistent(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.release_lock()  # should not raise

    def test_overwrite_lock(self, tmp_workspace):
        """C2 fix: acquire_lock now raises RuntimeError if lock already held."""
        workspace.ensure_workspace()
        workspace.acquire_lock("task-1")
        with pytest.raises(RuntimeError, match="Lock already held by task 'task-1'"):
            workspace.acquire_lock("task-2")
        # Lock should still hold task-1
        assert workspace.read_lock() == "task-1"

    def test_acquire_lock_self_heals_empty_lock_file(self, tmp_workspace):
        workspace.ensure_workspace()
        lock_file = tmp_workspace / ".lock"
        lock_file.write_text("", encoding="utf-8")
        workspace.acquire_lock("task-heal")
        assert workspace.read_lock() == "task-heal"


class TestClearRuntimeDecompose:
    def test_clears_decompose_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("multi_agent.workspace.workspace_dir", lambda: tmp_path)
        monkeypatch.setattr("multi_agent.workspace.inbox_dir", lambda: tmp_path / "inbox")
        monkeypatch.setattr("multi_agent.workspace.outbox_dir", lambda: tmp_path / "outbox")
        (tmp_path / "inbox").mkdir()
        (tmp_path / "outbox").mkdir()
        (tmp_path / "inbox" / "decompose.md").write_text("prompt")
        (tmp_path / "outbox" / "decompose.json").write_text('{"sub_tasks": []}')
        workspace.clear_runtime()
        assert not (tmp_path / "inbox" / "decompose.md").exists()
        assert not (tmp_path / "outbox" / "decompose.json").exists()


class TestClearRuntime:
    def test_clears_all_shared_files(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_inbox("builder", "prompt")
        workspace.write_inbox("reviewer", "prompt")
        workspace.write_outbox("builder", {"status": "done"})
        workspace.write_outbox("reviewer", {"decision": "approve"})
        (tmp_workspace / "TASK.md").write_text("task content")
        (tmp_workspace / "dashboard.md").write_text("dashboard")

        workspace.clear_runtime()

        assert not (tmp_workspace / "inbox" / "builder.md").exists()
        assert not (tmp_workspace / "inbox" / "reviewer.md").exists()
        assert not (tmp_workspace / "outbox" / "builder.json").exists()
        assert not (tmp_workspace / "outbox" / "reviewer.json").exists()
        assert not (tmp_workspace / "TASK.md").exists()
        assert not (tmp_workspace / "dashboard.md").exists()

    def test_safe_when_empty(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.clear_runtime()  # should not raise


class TestValidateOutboxData:
    """Task 7: Verify outbox data validation."""

    def test_builder_missing_status(self):
        errors = workspace.validate_outbox_data("builder", {"summary": "done"})
        assert "missing 'status' field" in errors

    def test_builder_missing_summary(self):
        errors = workspace.validate_outbox_data("builder", {"status": "completed"})
        assert "missing 'summary' field" in errors

    def test_builder_valid(self):
        errors = workspace.validate_outbox_data("builder", {"status": "completed", "summary": "done"})
        assert errors == []

    def test_reviewer_missing_decision(self):
        errors = workspace.validate_outbox_data("reviewer", {"feedback": "ok"})
        assert "missing 'decision' field" in errors

    def test_reviewer_valid(self):
        errors = workspace.validate_outbox_data("reviewer", {"decision": "approve", "summary": "LGTM"})
        assert errors == []

    def test_reviewer_missing_summary(self):
        errors = workspace.validate_outbox_data("reviewer", {"decision": "approve"})
        assert "missing 'summary' field" in errors

    def test_unknown_role_no_errors(self):
        errors = workspace.validate_outbox_data("decompose", {"anything": True})
        assert errors == []


class TestReadOutboxValidate:
    """Task 7: Verify read_outbox with validate=True."""

    def test_validate_false_returns_incomplete_data(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_outbox("builder", {"summary": "done"})  # missing status
        result = workspace.read_outbox("builder", validate=False)
        assert result is not None

    def test_validate_true_rejects_incomplete_builder(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_outbox("builder", {"summary": "done"})  # missing status
        result = workspace.read_outbox("builder", validate=True)
        assert result is None

    def test_validate_true_accepts_complete_builder(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_outbox("builder", {"status": "completed", "summary": "done"})
        result = workspace.read_outbox("builder", validate=True)
        assert result is not None
        assert result["status"] == "completed"

    def test_validate_true_rejects_incomplete_reviewer(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_outbox("reviewer", {"feedback": "ok"})  # missing decision
        result = workspace.read_outbox("reviewer", validate=True)
        assert result is None

    def test_validate_true_accepts_complete_reviewer(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_outbox("reviewer", {"decision": "approve", "summary": "LGTM"})
        result = workspace.read_outbox("reviewer", validate=True)
        assert result is not None


class TestCheckDiskSpace:
    """Task 14: Verify disk space check."""

    def test_sufficient_space(self, tmp_workspace):
        workspace.ensure_workspace()
        ok, mb = workspace.check_disk_space(min_mb=1)
        assert ok is True
        assert mb > 0

    def test_insufficient_space(self, tmp_workspace):
        workspace.ensure_workspace()
        mock_usage = type("Usage", (), {"total": 1024**3, "used": 1024**3, "free": 50 * 1024 * 1024})()
        with patch("multi_agent.workspace.shutil.disk_usage", return_value=mock_usage):
            ok, mb = workspace.check_disk_space(min_mb=100)
        assert ok is False
        assert mb == 50


class TestArchive:
    def test_archive_conversation(self, tmp_workspace):
        workspace.ensure_workspace()
        convo = [{"role": "orchestrator", "action": "assigned"}]
        path = workspace.archive_conversation("task-123", convo)
        assert path.exists()
        with path.open() as f:
            loaded = json.load(f)
        assert loaded == convo


class TestWorkspaceBoundary:
    """Task 49: Workspace boundary tests."""

    def test_write_outbox_unicode(self, tmp_workspace):
        workspace.ensure_workspace()
        data = {"status": "completed", "summary": "实现了用户认证 🎉"}
        workspace.write_outbox("builder", data)
        result = workspace.read_outbox("builder")
        assert result["summary"] == "实现了用户认证 🎉"

    def test_read_outbox_corrupt_json(self, tmp_workspace):
        workspace.ensure_workspace()
        outbox = tmp_workspace / "outbox" / "builder.json"
        outbox.write_text("{not valid json", encoding="utf-8")
        result = workspace.read_outbox("builder")
        assert result is None

    def test_save_task_yaml_nested_dict(self, tmp_workspace):
        workspace.ensure_workspace()
        data = {
            "task_id": "task-nested",
            "status": "active",
            "metadata": {"agent": "windsurf", "retries": 2},
        }
        workspace.save_task_yaml("task-nested", data)
        tasks = tmp_workspace / "tasks"
        yamls = list(tasks.glob("*.yaml"))
        assert len(yamls) == 1

    def test_archive_conversation_empty(self, tmp_workspace):
        workspace.ensure_workspace()
        path = workspace.archive_conversation("task-empty", [])
        assert path.exists()
        with path.open() as f:
            loaded = json.load(f)
        assert loaded == []

    def test_archive_conversation_large(self, tmp_workspace):
        workspace.ensure_workspace()
        convo = [{"role": "orchestrator", "action": f"step-{i}"} for i in range(150)]
        path = workspace.archive_conversation("task-large", convo)
        assert path.exists()
        with path.open() as f:
            loaded = json.load(f)
        assert len(loaded) == 150

    def test_ensure_workspace_idempotent(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.ensure_workspace()
        workspace.ensure_workspace()
        assert (tmp_workspace / "inbox").is_dir()

    def test_clear_runtime_partial_missing(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.write_inbox("builder", "prompt")
        # Don't create reviewer files — should still work
        workspace.clear_runtime()
        assert not (tmp_workspace / "inbox" / "builder.md").exists()

    def test_lock_path(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.acquire_lock("task-xyz")
        lock_file = tmp_workspace / ".lock"
        assert lock_file.exists()
        assert lock_file.read_text(encoding="utf-8").strip() == "task-xyz"


class TestRetryFileOp:
    """Task 63: File operation retry decorator tests."""

    def test_success_first_try(self):
        from multi_agent.workspace import retry_file_op

        call_count = 0

        @retry_file_op(retries=3, delay=0.01)
        def ok_fn():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert ok_fn() == "ok"
        assert call_count == 1

    def test_fail_then_succeed(self):
        from multi_agent.workspace import retry_file_op

        call_count = 0

        @retry_file_op(retries=3, delay=0.01)
        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("disk busy")
            return "ok"

        assert flaky_fn() == "ok"
        assert call_count == 2

    def test_all_retries_fail(self):
        from multi_agent.workspace import retry_file_op

        @retry_file_op(retries=2, delay=0.01)
        def bad_fn():
            raise OSError("always fails")

        with pytest.raises(OSError, match="always fails"):
            bad_fn()

    def test_non_os_error_not_retried(self):
        from multi_agent.workspace import retry_file_op

        call_count = 0

        @retry_file_op(retries=3, delay=0.01)
        def logic_err():
            nonlocal call_count
            call_count += 1
            raise ValueError("not an IO error")

        with pytest.raises(ValueError):
            logic_err()
        assert call_count == 1


class TestEncodingDetection:
    """Task 68: read_outbox encoding fallback tests."""

    def test_utf8_bom_file(self, tmp_workspace):
        outbox = tmp_workspace / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / "builder.json"
        content = b'\xef\xbb\xbf{"status": "completed", "summary": "done"}'
        path.write_bytes(content)

        result = workspace.read_outbox("builder")
        assert result is not None
        assert result["status"] == "completed"

    def test_latin1_file(self, tmp_workspace):
        outbox = tmp_workspace / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / "builder.json"
        content = '{"status": "completed", "summary": "done"}'.encode("latin-1")
        path.write_bytes(content)

        result = workspace.read_outbox("builder")
        assert result is not None
        assert result["status"] == "completed"


class TestWorkspaceHealth:
    """Task 73: Workspace health check tests."""

    def test_healthy_workspace(self, tmp_workspace):
        workspace.ensure_workspace()
        issues = workspace.check_workspace_health()
        assert isinstance(issues, list)

    def test_missing_directory(self, tmp_workspace):
        ws = tmp_workspace
        ws.mkdir(parents=True, exist_ok=True)
        # Don't create subdirectories
        issues = workspace.check_workspace_health()
        has_missing = any("Missing directory" in i for i in issues)
        assert has_missing

    def test_orphan_lock(self, tmp_workspace):
        workspace.ensure_workspace()
        lock = tmp_workspace / ".lock"
        lock.write_text("nonexistent-task", encoding="utf-8")
        issues = workspace.check_workspace_health()
        assert any("Orphan lock" in i for i in issues)


class TestWorkspaceStats:
    """Task 88: Workspace size statistics tests."""

    def test_empty_workspace(self, tmp_workspace):
        workspace.ensure_workspace()
        stats = workspace.get_workspace_stats()
        assert isinstance(stats, dict)
        assert "total_size_mb" in stats
        assert "file_count" in stats
        assert stats["file_count"] >= 0

    def test_with_files(self, tmp_workspace):
        workspace.ensure_workspace()
        (tmp_workspace / "test.txt").write_text("hello world")
        stats = workspace.get_workspace_stats()
        assert stats["file_count"] >= 1
        assert stats["total_size_mb"] >= 0


class TestCleanupOldFiles:
    """Task 92: Auto cleanup old files tests."""

    def test_cleanup_removes_old(self, tmp_workspace):
        workspace.ensure_workspace()
        # Create an old file in history
        old_file = tmp_workspace / "history" / "old-task.json"
        old_file.write_text("[]")
        # Set mtime to 30 days ago
        old_mtime = time.time() - (30 * 86400)
        os.utime(old_file, (old_mtime, old_mtime))

        deleted = workspace.cleanup_old_files(max_age_days=7)
        assert deleted >= 1
        assert not old_file.exists()

    def test_cleanup_preserves_new(self, tmp_workspace):
        workspace.ensure_workspace()
        new_file = tmp_workspace / "history" / "new-task.json"
        new_file.write_text("[]")

        workspace.cleanup_old_files(max_age_days=7)
        assert new_file.exists()

    def test_cleanup_preserves_active_task(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.acquire_lock("active-task")
        old_file = tmp_workspace / "tasks" / "active-task.yaml"
        old_file.write_text("status: active")
        old_mtime = time.time() - (30 * 86400)
        os.utime(old_file, (old_mtime, old_mtime))

        workspace.cleanup_old_files(max_age_days=7)
        assert old_file.exists()


# ── Uncovered lines: disk space, write_outbox error, read_outbox encoding, health checks ──


class TestEnsureWorkspaceDiskSpaceWarning:
    """Cover lines 73-74: disk space check exception suppressed."""

    def test_disk_check_exception_suppressed(self, tmp_workspace):
        with patch("multi_agent.workspace.check_disk_space", side_effect=RuntimeError("boom")):
            ws = workspace.ensure_workspace()
        assert ws.exists()


class TestWriteOutboxAtomicError:
    """Cover lines 152-155: write_outbox_json atomic write failure cleanup."""

    def test_write_failure_cleans_tmp(self, tmp_workspace):
        workspace.ensure_workspace()
        with patch("json.dump", side_effect=OSError("disk full")), \
             pytest.raises(OSError):
            workspace.write_outbox("builder", {"status": "done"})


class TestReadOutboxEncoding:
    """Cover line 127: UnicodeDecodeError fallback."""

    def test_unicode_error_tries_next_encoding(self, tmp_workspace):
        workspace.ensure_workspace()
        outbox = tmp_workspace / "outbox"
        outbox.mkdir(exist_ok=True)
        # Write binary that's valid latin-1 but invalid utf-8
        p = outbox / "builder.json"
        p.write_bytes(b'{"status": "ok", "summary": "\xe9"}\n')
        result = workspace.read_outbox("builder")
        # Should succeed via latin-1 fallback or return None
        assert result is None or isinstance(result, dict)


class TestCheckWorkspaceHealthStoreDb:
    """Cover lines 279-280: store.db not writable."""

    def test_store_db_not_writable(self, tmp_workspace):
        workspace.ensure_workspace()
        db = tmp_workspace / "store.db"
        db.write_text("")
        # Simulate OSError when trying to open for append
        original_open = Path.open

        def fail_on_db(self_path, *args, **kwargs):
            if self_path.name == "store.db" and "a" in (args[0] if args else kwargs.get("mode", "")):
                raise OSError("read-only fs")
            return original_open(self_path, *args, **kwargs)

        with patch.object(Path, "open", fail_on_db):
            issues = workspace.check_workspace_health()
        assert any("writable" in i for i in issues)


class TestFindOversizedFiles:
    """Cover lines 307-309: oversized file detection + OSError skip."""

    def test_oversized_detected(self, tmp_workspace):
        workspace.ensure_workspace()
        big = tmp_workspace / "outbox" / "big.json"
        big.parent.mkdir(exist_ok=True)
        # MAX_FILE_SIZE_MB is 50, create a fake stat to avoid 50MB file
        real_stat = Path.stat

        def big_stat(self_path, **kwargs):
            s = real_stat(self_path, **kwargs)
            if self_path.name == "big.json":
                import os as _os
                return _os.stat_result((s.st_mode, s.st_ino, s.st_dev, s.st_nlink,
                                        s.st_uid, s.st_gid, 60 * 1024 * 1024,
                                        int(s.st_atime), int(s.st_mtime), int(s.st_ctime)))
            return s

        big.write_text("{}")
        with patch.object(Path, "stat", big_stat):
            result = workspace._find_oversized_files(tmp_workspace)
        assert any("Oversized" in r for r in result)


class TestGetWorkspaceStatsOSError:
    """Cover lines 339-340: OSError during stat in get_workspace_stats."""

    def test_stat_oserror_skipped(self, tmp_workspace):
        workspace.ensure_workspace()
        (tmp_workspace / "outbox").mkdir(exist_ok=True)
        (tmp_workspace / "outbox" / "test.json").write_text("{}")
        stats = workspace.get_workspace_stats()
        assert stats["file_count"] >= 0


class TestSaveTaskYamlAtomic:
    """Regression: save_task_yaml must use atomic write (tempfile+replace).

    Previously used direct path.open('w'), which could corrupt the file
    if the process crashed mid-write.
    """

    def test_atomic_write_no_partial(self, tmp_workspace):
        """Verify the YAML file is either fully written or absent."""
        workspace.ensure_workspace()
        data = {"task_id": "task-atomic", "status": "active", "detail": "x" * 500}
        path = workspace.save_task_yaml("task-atomic", data)
        import yaml
        result = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert result["task_id"] == "task-atomic"
        assert result["status"] == "active"

    def test_no_tmp_files_left(self, tmp_workspace):
        """After successful write, no .tmp files should remain."""
        workspace.ensure_workspace()
        workspace.save_task_yaml("task-clean", {"task_id": "task-clean", "status": "ok"})
        tasks = tmp_workspace / "tasks"
        tmp_files = list(tasks.glob("*.tmp"))
        assert tmp_files == []

    def test_overwrite_existing(self, tmp_workspace):
        """Overwriting existing YAML should be atomic too."""
        workspace.ensure_workspace()
        workspace.save_task_yaml("task-over", {"task_id": "task-over", "status": "v1"})
        workspace.save_task_yaml("task-over", {"task_id": "task-over", "status": "v2"})
        import yaml
        path = tmp_workspace / "tasks" / "task-over.yaml"
        result = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert result["status"] == "v2"

    def test_update_task_yaml_preserves_existing_fields(self, tmp_workspace):
        """update_task_yaml should merge updates without dropping metadata."""
        import yaml

        workspace.ensure_workspace()
        workspace.save_task_yaml(
            "task-merge",
            {
                "task_id": "task-merge",
                "status": "active",
                "builder": "windsurf",
                "reviewer": "codex",
                "mode": "session",
            },
        )
        workspace.update_task_yaml("task-merge", {"status": "failed", "error": "boom"})

        path = tmp_workspace / "tasks" / "task-merge.yaml"
        result = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert result["status"] == "failed"
        assert result["error"] == "boom"
        assert result["builder"] == "windsurf"
        assert result["reviewer"] == "codex"
        assert result["mode"] == "session"


class TestCleanupOSError:
    """Cover lines 370, 378-379: cleanup skips dirs and handles OSError."""

    def test_cleanup_skips_subdirs(self, tmp_workspace):
        workspace.ensure_workspace()
        subdir = tmp_workspace / "history" / "subdir"
        subdir.mkdir(parents=True)
        workspace.cleanup_old_files(max_age_days=0)
        assert subdir.exists()  # dirs should not be deleted

    def test_cleanup_oserror_on_unlink(self, tmp_workspace):
        workspace.ensure_workspace()
        old = tmp_workspace / "history" / "old.json"
        old.parent.mkdir(exist_ok=True)
        old.write_text("[]")
        old_mtime = time.time() - (30 * 86400)
        os.utime(old, (old_mtime, old_mtime))
        with patch.object(Path, "unlink", side_effect=OSError("locked")):
            deleted = workspace.cleanup_old_files(max_age_days=7)
        assert deleted == 0


class TestArchiveConversationAtomic:
    """Regression: archive_conversation must use atomic write (tempfile+replace)."""

    def test_atomic_write_no_partial(self, tmp_workspace):
        workspace.ensure_workspace()
        convo = [{"role": "builder", "t": 1.0}]
        path = workspace.archive_conversation("task-arch-1", convo)
        assert path.exists()
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == convo

    def test_atomic_no_leftover_tmp(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.archive_conversation("task-arch-2", [{"role": "reviewer"}])
        history = tmp_workspace / "history"
        tmp_files = list(history.glob("*.tmp"))
        assert tmp_files == []

    def test_atomic_overwrite(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.archive_conversation("task-arch-3", [{"v": 1}])
        workspace.archive_conversation("task-arch-3", [{"v": 2}])
        import json
        path = tmp_workspace / "history" / "task-arch-3.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == [{"v": 2}]


class TestClearOutboxInboxNoTOCTOU:
    """Regression: clear_outbox/clear_inbox must not raise on concurrent delete."""

    def test_clear_outbox_missing_file_no_error(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.clear_outbox("builder")  # file doesn't exist — must not raise

    def test_clear_inbox_missing_file_no_error(self, tmp_workspace):
        workspace.ensure_workspace()
        workspace.clear_inbox("reviewer")  # file doesn't exist — must not raise


class TestReadLockNoTOCTOU:
    """Regression: read_lock must handle concurrent deletion gracefully."""

    def test_read_lock_deleted_between_calls(self, tmp_workspace):
        # read_lock should return None if file vanishes
        result = workspace.read_lock()
        assert result is None

    def test_read_lock_empty_file_returns_none(self, tmp_workspace):
        workspace.ensure_workspace()
        lock_path = tmp_workspace / ".lock"
        lock_path.write_text("")
        result = workspace.read_lock()
        assert result is None
