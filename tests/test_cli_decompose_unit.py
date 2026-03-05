"""Unit tests for cli_decompose.py — _read_decompose_file, _collect_sub_result,
_DecomposeExecContext, _finalize_decompose, _validate_and_sort, _display_sub_tasks,
_load_decompose_checkpoint, _obtain_decompose_result."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from multi_agent.cli_decompose import (
    _collect_sub_result,
    _DecomposeExecContext,
    _display_sub_tasks,
    _finalize_decompose,
    _load_decompose_checkpoint,
    _obtain_decompose_result,
    _read_decompose_file,
    _retry_sub_task,
    _validate_and_sort,
)
from multi_agent.schema import DecomposeResult, SubTask


# ── helpers ──────────────────────────────────────────────


def _sub(id: str, desc: str = "desc", deps: list[str] | None = None) -> SubTask:
    return SubTask(id=id, description=desc, deps=deps or [])


def _decompose_result(tasks: list[SubTask] | None = None) -> DecomposeResult:
    return DecomposeResult(
        sub_tasks=tasks or [_sub("a"), _sub("b", deps=["a"])],
        reasoning="test reason",
    )


# ── _read_decompose_file ────────────────────────────────


class TestReadDecomposeFile:
    def test_reads_json(self, tmp_path):
        f = tmp_path / "dec.json"
        data = {"sub_tasks": [{"id": "a", "description": "A"}], "reasoning": "r"}
        f.write_text(json.dumps(data), encoding="utf-8")
        with patch("multi_agent.workspace.release_lock"):
            result = _read_decompose_file(str(f))
        assert result.sub_tasks[0].id == "a"

    def test_reads_yaml(self, tmp_path):
        f = tmp_path / "dec.yaml"
        f.write_text("sub_tasks:\n  - id: x\n    description: X\nreasoning: r\n", encoding="utf-8")
        with patch("multi_agent.workspace.release_lock"):
            result = _read_decompose_file(str(f))
        assert result.sub_tasks[0].id == "x"

    def test_invalid_file_exits(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        with patch("multi_agent.workspace.release_lock"), \
             pytest.raises(SystemExit):
            _read_decompose_file(str(f))

    def test_missing_file_exits(self, tmp_path):
        with patch("multi_agent.workspace.release_lock"), \
             pytest.raises(SystemExit):
            _read_decompose_file(str(tmp_path / "nonexistent.json"))


# ── _collect_sub_result ──────────────────────────────────


class TestCollectSubResult:
    def test_collects_approved(self):
        snapshot = MagicMock()
        snapshot.values = {
            "final_status": "approved",
            "builder_output": {"summary": "Done", "changed_files": ["/a.py"]},
            "reviewer_output": {"feedback": "LGTM"},
            "retry_count": 0,
        }
        app = MagicMock()
        app.get_state.return_value = snapshot
        st = _sub("a")
        result = _collect_sub_result(app, {"configurable": {"thread_id": "t"}}, st, time.time() - 5)
        assert result["sub_id"] == "a"
        assert result["status"] == "approved"
        assert result["summary"] == "Done"
        assert result["changed_files"] == ["/a.py"]
        assert result["reviewer_feedback"] == "LGTM"

    def test_none_snapshot(self):
        app = MagicMock()
        app.get_state.return_value = None
        st = _sub("b")
        result = _collect_sub_result(app, {}, st, time.time())
        assert result["status"] == "unknown"

    def test_non_dict_builder_output(self):
        snapshot = MagicMock()
        snapshot.values = {"builder_output": "not a dict", "reviewer_output": 123}
        app = MagicMock()
        app.get_state.return_value = snapshot
        st = _sub("c")
        result = _collect_sub_result(app, {}, st, time.time())
        assert result["summary"] == ""
        assert result["changed_files"] == []


# ── _DecomposeExecContext ────────────────────────────────


class TestDecomposeExecContext:
    def _make_ctx(self, **overrides: Any) -> _DecomposeExecContext:
        defaults = dict(
            app=MagicMock(), parent_task_id="parent-1",
            builder="ws", reviewer="cursor", timeout=60, retry_budget=2,
            workflow_mode="normal", review_policy={},
            no_watch=False, auto_confirm=True,
            make_config=lambda tid: {"configurable": {"thread_id": tid}},
            build_state=MagicMock(return_value={"task_id": "sub-1"}),
            start_task=MagicMock(), start_error=RuntimeError,
            show_waiting=MagicMock(), watch_loop=MagicMock(),
            save_yaml=MagicMock(), save_ckpt=MagicMock(), clear_rt=MagicMock(),
        )
        defaults.update(overrides)
        return _DecomposeExecContext(**defaults)

    def test_skip_completed(self, capsys):
        ctx = self._make_ctx()
        action = ctx.run_one(1, 3, _sub("a"), [], {"a"}, set(), [])
        assert action is None
        assert "已完成" in capsys.readouterr().out

    def test_skip_failed_dependency(self, capsys):
        st = _sub("b", deps=["a"])
        prior: list[dict[str, Any]] = []
        failed = {"a"}
        ctx = self._make_ctx()
        action = ctx.run_one(1, 3, st, prior, set(), failed, [])
        assert action is None
        assert len(prior) == 1
        assert prior[0]["status"] == "skipped"
        assert "b" in failed

    def test_start_error_records_failure(self, capsys):
        ctx = self._make_ctx(start_task=MagicMock(side_effect=RuntimeError("boom")))
        prior: list[dict[str, Any]] = []
        failed: set[str] = set()
        action = ctx.run_one(1, 3, _sub("a"), prior, set(), failed, [])
        assert action is None
        assert prior[0]["status"] == "failed"
        assert "a" in failed

    def test_no_watch_returns_early(self, capsys):
        ctx = self._make_ctx(no_watch=True)
        sorted_tasks = [_sub("a")]
        action = ctx.run_one(1, 1, _sub("a"), [], set(), set(), sorted_tasks)
        assert action == "return"

    def test_approved_completes(self):
        app = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"final_status": "approved", "builder_output": {"summary": "ok"}, "reviewer_output": {}}
        app.get_state.return_value = snapshot
        ctx = self._make_ctx(app=app)
        prior: list[dict[str, Any]] = []
        completed: set[str] = set()
        action = ctx.run_one(1, 1, _sub("a"), prior, completed, set(), [])
        assert action is None
        assert "a" in completed
        assert prior[0]["status"] == "approved"

    def test_failure_auto_confirm_skips(self):
        """auto_confirm=True → failed sub-task is silently added to failed_ids."""
        app = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"final_status": "failed", "builder_output": {}, "reviewer_output": {}}
        app.get_state.return_value = snapshot
        ctx = self._make_ctx(app=app, auto_confirm=True)
        prior: list[dict[str, Any]] = []
        failed: set[str] = set()
        action = ctx.run_one(1, 1, _sub("a"), prior, set(), failed, [])
        assert action is None
        assert "a" in failed


# ── _validate_and_sort ───────────────────────────────────


class TestValidateAndSort:
    def test_valid_result(self):
        dr = _decompose_result([_sub("a"), _sub("b")])
        result = _validate_and_sort(dr, MagicMock(), MagicMock())
        assert result is not None
        assert len(result) == 2

    def test_empty_subtasks_returns_none(self):
        dr = DecomposeResult(sub_tasks=[], reasoning="")
        result = _validate_and_sort(dr, MagicMock(), MagicMock())
        assert result is None

    def test_circular_deps_exits(self):
        dr = _decompose_result([_sub("a", deps=["b"]), _sub("b", deps=["a"])])
        with pytest.raises(SystemExit):
            _validate_and_sort(dr, MagicMock(), MagicMock())


# ── _display_sub_tasks ───────────────────────────────────


class TestDisplaySubTasks:
    def test_displays_tasks_with_reasoning(self, capsys):
        tasks = [_sub("a"), _sub("b", deps=["a"])]
        dr = _decompose_result(tasks)
        _display_sub_tasks(dr, tasks)
        out = capsys.readouterr().out
        assert "2 个子任务" in out
        assert "test reason" in out

    def test_no_reasoning(self, capsys):
        tasks = [_sub("a")]
        dr = DecomposeResult(sub_tasks=tasks, reasoning="")
        _display_sub_tasks(dr, tasks)
        out = capsys.readouterr().out
        assert "1 个子任务" in out


# ── _load_decompose_checkpoint ───────────────────────────


class TestLoadDecomposeCheckpoint:
    @patch("multi_agent.meta_graph.load_checkpoint", return_value=None)
    def test_no_checkpoint(self, mock_load):
        prior, completed, failed = _load_decompose_checkpoint("p-1")
        assert prior == []
        assert completed == set()
        assert failed == set()

    @patch("multi_agent.meta_graph.load_checkpoint")
    def test_with_checkpoint(self, mock_load, capsys):
        mock_load.return_value = {
            "prior_results": [
                {"sub_id": "a", "status": "approved"},
                {"sub_id": "b", "status": "failed"},
            ],
            "completed_ids": ["a", "b"],
        }
        prior, completed, failed = _load_decompose_checkpoint("p-1")
        assert len(prior) == 2
        assert "a" in completed
        assert "b" in failed
        assert "checkpoint" in capsys.readouterr().out.lower()


# ── _obtain_decompose_result ─────────────────────────────


class TestObtainDecomposeResult:
    @patch("multi_agent.decompose.get_cached_decompose")
    def test_cache_hit(self, mock_cache, capsys):
        mock_cache.return_value = _decompose_result()
        result = _obtain_decompose_result("req", "code-implement", "ws", 60)
        assert result is not None
        assert "缓存" in capsys.readouterr().out

    @patch("multi_agent.decompose.get_cached_decompose", return_value=None)
    def test_no_cache_with_file(self, mock_cache, tmp_path):
        f = tmp_path / "d.json"
        data = {"sub_tasks": [{"id": "a", "description": "A"}], "reasoning": "r"}
        f.write_text(json.dumps(data), encoding="utf-8")
        with patch("multi_agent.workspace.release_lock"):
            result = _obtain_decompose_result("req", "code-implement", "ws", 60, decompose_file=str(f))
        assert result is not None

    def test_no_cache_flag_skips_cache(self):
        """When no_cache=True, decompose cache should not be consulted."""
        # no_cache=True → _obtain_decompose_result does not import get_cached_decompose
        # It should go straight to decompose_file or wait
        with patch("multi_agent.cli_decompose._wait_for_decompose_agent", return_value=_decompose_result()):
            result = _obtain_decompose_result("req", "code-implement", "ws", 60, no_cache=True)
        assert result is not None


# ── _finalize_decompose ──────────────────────────────────


class TestFinalizeDecompose:
    @patch("multi_agent.config.workspace_dir")
    def test_writes_report(self, mock_ws, tmp_path, capsys):
        mock_ws.return_value = tmp_path
        agg = {
            "total_sub_tasks": 2, "completed": 2, "total_retries": 0,
            "failed": [], "all_changed_files": ["/a.py"],
            "final_status": "approved",
        }
        aggregate_fn = MagicMock(return_value=agg)
        with patch("multi_agent.meta_graph.generate_aggregate_report", return_value="# Report"):
            _finalize_decompose(
                "p-1", [{"sub_id": "a"}], time.time() - 10,
                aggregate_fn, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            )
        assert (tmp_path / "report-p-1.md").exists()
        out = capsys.readouterr().out
        assert "✅ 全部通过" in out

    @patch("multi_agent.config.workspace_dir")
    def test_shows_failures(self, mock_ws, tmp_path, capsys):
        mock_ws.return_value = tmp_path
        agg = {
            "total_sub_tasks": 2, "completed": 1, "total_retries": 1,
            "failed": ["b"], "all_changed_files": [],
            "final_status": "failed",
        }
        aggregate_fn = MagicMock(return_value=agg)
        with patch("multi_agent.meta_graph.generate_aggregate_report", return_value="# Report"):
            _finalize_decompose(
                "p-1", [], time.time() - 120,
                aggregate_fn, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
            )
        out = capsys.readouterr().out
        assert "❌" in out
        assert "b" in out
        assert "分" in out  # shows minutes when > 60s


# ── _retry_sub_task ──────────────────────────────────────


class TestRetrySubTask:
    def test_retry_returns_result(self):
        app = MagicMock()
        snapshot = MagicMock()
        snapshot.values = {"final_status": "approved", "builder_output": {"summary": "fixed"}, "reviewer_output": {}}
        app.get_state.return_value = snapshot

        result = _retry_sub_task(
            app, _sub("a"), "parent-1",
            "ws", "cursor", 60, 2, [],
            "normal", {}, time.time(),
            lambda tid: {"configurable": {"thread_id": tid}},
            MagicMock(return_value={"task_id": "sub-retry"}),
            MagicMock(),  # start_fn
            RuntimeError,  # start_error_cls
            MagicMock(),  # show_waiting
            MagicMock(),  # watch_loop
        )
        assert result["status"] == "approved"
