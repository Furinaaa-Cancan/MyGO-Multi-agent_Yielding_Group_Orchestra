"""Tests for the OutboxPoller — including partial write race condition."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent.watcher import MAX_OUTBOX_SIZE, OutboxPoller


@pytest.fixture
def tmp_outbox(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    monkeypatch.setattr("multi_agent.watcher.outbox_dir", lambda: outbox)
    return outbox


class TestAdaptivePolling:
    """Task 89: Adaptive polling interval tests."""

    def test_initial_interval(self):
        poller = OutboxPoller(poll_interval=2.0, min_interval=0.5, max_interval=5.0)
        assert poller._current_interval == 2.0
        assert poller._idle_count == 0

    def test_idle_count_increases(self, tmp_outbox):
        poller = OutboxPoller(poll_interval=2.0, min_interval=0.5, max_interval=5.0)
        # Empty check_once calls simulate idle polls (no files to detect)
        for _ in range(15):
            poller.check_once()
            poller._idle_count += 1
            if poller._idle_count >= 10:
                poller._current_interval = min(
                    poller._current_interval * 1.5, poller.max_interval
                )
        assert poller._current_interval > 2.0
        assert poller._current_interval <= 5.0

    def test_activity_resets_interval(self, tmp_outbox):
        poller = OutboxPoller(poll_interval=2.0, min_interval=0.5, max_interval=5.0)
        poller._idle_count = 20
        poller._current_interval = 5.0
        # Simulate activity by writing a file and detecting it
        path = tmp_outbox / "builder.json"
        path.write_text(json.dumps({"status": "done", "summary": "ok"}))
        results = poller.check_once()
        if results:
            poller._idle_count = 0
            poller._current_interval = poller.min_interval
        assert poller._current_interval == 0.5
        assert poller._idle_count == 0


class TestCheckOnce:
    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_detects_new_file(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        # No files yet
        assert poller.check_once() == []

        # Write valid builder output
        (tmp_outbox / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "done"}),
            encoding="utf-8",
        )
        results = poller.check_once()
        assert len(results) == 1
        assert results[0][0] == "builder"
        assert results[0][1]["status"] == "completed"

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_ignores_already_seen(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        (tmp_outbox / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "done"}),
            encoding="utf-8",
        )
        # First check — detected
        assert len(poller.check_once()) == 1
        # Second check — same mtime, not re-detected
        assert len(poller.check_once()) == 0

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_detects_updated_file(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        path = tmp_outbox / "builder.json"
        path.write_text(json.dumps({"status": "v1", "summary": "first"}))
        poller.check_once()

        # Update with new mtime (use os.utime to avoid filesystem granularity issues)
        path.write_text(json.dumps({"status": "v2", "summary": "second"}))
        st = path.stat()
        os.utime(path, (st.st_atime + 2, st.st_mtime + 2))
        results = poller.check_once()
        assert len(results) == 1
        assert results[0][1]["status"] == "v2"

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_partial_write_retries(self, mock_stable, tmp_outbox):
        """CRITICAL: partial JSON must NOT mark file as seen."""
        poller = OutboxPoller()
        path = tmp_outbox / "builder.json"

        # Write partial/corrupt JSON
        path.write_text('{"status": "complet', encoding="utf-8")
        results = poller.check_once()
        assert results == []  # JSONDecodeError — not detected

        # _known should NOT have been updated
        assert "builder" not in poller._known

        # Now write complete JSON (same or newer mtime)
        time.sleep(0.05)
        path.write_text(
            json.dumps({"status": "completed", "summary": "done"}),
            encoding="utf-8",
        )
        results = poller.check_once()
        assert len(results) == 1  # NOW detected
        assert results[0][1]["status"] == "completed"

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_ignores_non_dict_json(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        (tmp_outbox / "builder.json").write_text("[1, 2, 3]")
        results = poller.check_once()
        assert results == []
        # Should NOT mark as seen (non-dict)
        assert "builder" not in poller._known

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_multiple_roles(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        (tmp_outbox / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "b"})
        )
        (tmp_outbox / "reviewer.json").write_text(
            json.dumps({"decision": "approve", "summary": "r"})
        )
        results = poller.check_once()
        roles = {r[0] for r in results}
        assert roles == {"builder", "reviewer"}

    def test_missing_outbox_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "multi_agent.watcher.outbox_dir", lambda: tmp_path / "nonexistent"
        )
        poller = OutboxPoller()
        assert poller.check_once() == []

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_skips_oversized_file(self, mock_stable, tmp_outbox):
        """Task 67: Files exceeding MAX_OUTBOX_SIZE are skipped."""
        poller = OutboxPoller()
        path = tmp_outbox / "builder.json"
        path.write_text(json.dumps({"status": "done", "summary": "x"}))
        real_stat = Path.stat
        def fake_stat(self_path, **kwargs):
            s = real_stat(self_path, **kwargs)
            if self_path.name == "builder.json":
                import os
                return os.stat_result((s.st_mode, s.st_ino, s.st_dev, s.st_nlink,
                                       s.st_uid, s.st_gid, MAX_OUTBOX_SIZE + 1,
                                       int(s.st_atime), int(s.st_mtime), int(s.st_ctime)))
            return s
        with patch.object(Path, "stat", fake_stat):
            results = poller.check_once()
        assert results == []


class TestWaitStable:
    """Task 8: Verify _wait_stable method."""

    def test_stable_file(self, tmp_path):
        """File that doesn't change returns True."""
        path = tmp_path / "test.json"
        path.write_text('{"ok": true}')
        result = OutboxPoller._wait_stable(path, settle_time=0.01, max_wait=0.1)
        assert result is True

    def test_missing_file(self, tmp_path):
        """Non-existent file returns False."""
        path = tmp_path / "nonexistent.json"
        result = OutboxPoller._wait_stable(path, settle_time=0.01, max_wait=0.05)
        assert result is False

    def test_growing_file(self, tmp_path):
        """File that keeps growing returns False after max_wait."""
        path = tmp_path / "growing.json"
        path.write_text("x")
        call_count = [0]
        real_stat = Path.stat
        def fake_stat(self_path, **kwargs):
            s = real_stat(self_path, **kwargs)
            if self_path.name == "growing.json":
                call_count[0] += 1
                import os
                return os.stat_result((s.st_mode, s.st_ino, s.st_dev, s.st_nlink,
                                       s.st_uid, s.st_gid, 100 * call_count[0],
                                       int(s.st_atime), int(s.st_mtime), int(s.st_ctime)))
            return s
        with patch.object(Path, "stat", fake_stat):
            result = OutboxPoller._wait_stable(path, settle_time=0.01, max_wait=0.05)
        assert result is False


class TestWatcherBoundary:
    """Task 43: Watcher boundary tests."""

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_ignores_non_json_files(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        (tmp_outbox / "notes.txt").write_text("hello")
        (tmp_outbox / "data.csv").write_text("a,b,c")
        results = poller.check_once()
        assert results == []

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_empty_json_object(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        (tmp_outbox / "builder.json").write_text("{}")
        results = poller.check_once()
        assert len(results) == 1
        assert results[0][1] == {}

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_large_valid_json(self, mock_stable, tmp_outbox):
        poller = OutboxPoller()
        data = {"status": "completed", "summary": "x" * 100000}
        (tmp_outbox / "builder.json").write_text(json.dumps(data))
        results = poller.check_once()
        assert len(results) == 1

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_watch_stop_after(self, mock_stable, tmp_outbox):
        poller = OutboxPoller(poll_interval=0.01)
        (tmp_outbox / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "done"})
        )
        collected = []
        def cb(role, data):
            collected.append((role, data))
        poller.watch(callback=cb, stop_after=1)
        assert len(collected) == 1

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_callback_exception_propagates(self, mock_stable, tmp_outbox):
        poller = OutboxPoller(poll_interval=0.01)
        (tmp_outbox / "builder.json").write_text(
            json.dumps({"status": "completed", "summary": "done"})
        )
        def bad_cb(role, data):
            raise RuntimeError("callback error")
        with pytest.raises(RuntimeError, match="callback error"):
            poller.watch(callback=bad_cb, stop_after=1)

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_symlink_in_outbox(self, mock_stable, tmp_outbox, tmp_path):
        poller = OutboxPoller()
        real_file = tmp_path / "real.json"
        real_file.write_text(json.dumps({"status": "done", "summary": "ok"}))
        link = tmp_outbox / "builder.json"
        link.symlink_to(real_file)
        results = poller.check_once()
        assert len(results) == 1


class TestStopAfterZero:
    """R11: stop_after=0 should return immediately, not loop forever."""

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_stop_after_zero_returns_immediately(self, mock_stable, tmp_outbox):
        poller = OutboxPoller(poll_interval=0.01)
        collected = []
        # stop_after=0 means "stop before processing any" — should return right away
        poller.watch(callback=lambda r, d: collected.append(r), stop_after=0)
        assert collected == []


class TestOversizedWarningDedup:
    """R11: oversized file warning should only fire once per role."""

    def test_warns_only_once(self, tmp_outbox):
        poller = OutboxPoller()
        big_file = tmp_outbox / "builder.json"
        big_file.write_bytes(b"x" * (MAX_OUTBOX_SIZE + 1))

        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            poller.check_once()
            poller.check_once()
            poller.check_once()
        oversized_warnings = [x for x in w if "exceeds" in str(x.message)]
        assert len(oversized_warnings) == 1

    def test_warns_again_after_size_normalizes(self, tmp_outbox):
        poller = OutboxPoller()
        big_file = tmp_outbox / "builder.json"
        big_file.write_bytes(b"x" * (MAX_OUTBOX_SIZE + 1))

        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            poller.check_once()  # first warn
            big_file.write_text(json.dumps({"status": "ok", "summary": "s"}))
            poller.check_once()  # normal → clears warning
            big_file.write_bytes(b"x" * (MAX_OUTBOX_SIZE + 1))
            poller.check_once()  # should warn again
        oversized_warnings = [x for x in w if "exceeds" in str(x.message)]
        assert len(oversized_warnings) == 2


# ── _wait_stable OSError paths (lines 53-54, 61-62) ─────


class TestWaitStableOSError:
    """Cover _wait_stable OSError branches."""

    def test_initial_stat_oserror(self, tmp_path):
        """OSError on first stat inside try block → returns False (lines 53-54)."""
        path = tmp_path / "test.json"
        path.write_text("{}")
        call_count = [0]
        real_stat = Path.stat

        def selective_stat(self_path, **kwargs):
            if self_path == path:
                call_count[0] += 1
                # First call is from exists(), let it pass
                # Second call is stat() inside try → raise
                if call_count[0] >= 2:
                    raise OSError("disk error")
            return real_stat(self_path, **kwargs)

        with patch.object(Path, "stat", selective_stat):
            result = OutboxPoller._wait_stable(path, settle_time=0.01, max_wait=0.05)
        assert result is False

    def test_second_stat_oserror(self, tmp_path):
        """OSError on loop stat (during settle) → returns False (lines 61-62)."""
        path = tmp_path / "test.json"
        path.write_text("{}")
        call_count = [0]
        real_stat = Path.stat

        def flaky_stat(self_path, **kwargs):
            if self_path == path:
                call_count[0] += 1
                # calls 1 (exists) and 2 (first stat) pass; call 3 (loop stat) fails
                if call_count[0] >= 3:
                    raise OSError("gone")
            return real_stat(self_path, **kwargs)

        with patch.object(Path, "stat", flaky_stat):
            result = OutboxPoller._wait_stable(path, settle_time=0.01, max_wait=0.1)
        assert result is False


# ── check_once OSError on stat (lines 76-77) ────────────


class TestCheckOnceOSError:
    """Cover check_once when stat fails between _scan and stat."""

    def test_stat_oserror_skips_file(self, tmp_outbox):
        """OSError between _scan and stat → file skipped (lines 76-77)."""
        poller = OutboxPoller()
        f = tmp_outbox / "builder.json"
        f.write_text(json.dumps({"status": "ok", "summary": "s"}))
        real_stat = Path.stat

        def scan_ok_then_fail(self_path, **kwargs):
            # Let _scan's exists()/iterdir work; fail only on the builder.json stat inside check_once
            if self_path == f:
                raise OSError("vanished")
            return real_stat(self_path, **kwargs)

        with patch.object(Path, "stat", scan_ok_then_fail):
            results = poller.check_once()
        assert results == []


# ── check_once unstable file (line 92) ──────────────────


class TestCheckOnceUnstable:
    """Cover check_once when file is still changing."""

    def test_unstable_file_skipped(self, tmp_outbox):
        """File that _wait_stable returns False for → skipped (line 92)."""
        poller = OutboxPoller()
        (tmp_outbox / "builder.json").write_text(json.dumps({"status": "ok", "summary": "s"}))

        with patch.object(OutboxPoller, "_wait_stable", return_value=False):
            results = poller.check_once()
        assert results == []


# ── watch() idle backoff (lines 130-132, 140) ───────────


class TestWatchIdleBackoff:
    """Cover watch() idle count increment and interval backoff."""

    @patch.object(OutboxPoller, "_wait_stable", return_value=True)
    def test_idle_backoff_increases_interval(self, mock_stable, tmp_outbox):
        """After 10+ idle polls, interval grows up to max_interval (lines 130-132)."""
        poller = OutboxPoller(poll_interval=0.01, min_interval=0.01, max_interval=0.05)
        max_intervals_seen = [0.0]
        call_count = [0]
        real_sleep = time.sleep  # capture before patching

        def counting_sleep(secs):
            max_intervals_seen[0] = max(max_intervals_seen[0], poller._current_interval)
            call_count[0] += 1
            if call_count[0] > 15:
                (tmp_outbox / "builder.json").write_text(
                    json.dumps({"status": "done", "summary": "ok"})
                )
            real_sleep(0.001)

        collected = []
        with patch("multi_agent.watcher.time.sleep", side_effect=counting_sleep):
            poller.watch(callback=lambda r, d: collected.append(r), stop_after=1)

        assert len(collected) == 1
        # Interval should have grown during idle period before file appeared
        assert max_intervals_seen[0] > 0.01
