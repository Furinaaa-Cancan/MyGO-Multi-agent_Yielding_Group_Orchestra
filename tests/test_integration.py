"""Integration tests for full graph flow (Tasks 46-47)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestApproveFlow:
    """Task 46: plan → build → review(approve) → END."""

    def test_approve_flow_final_status(self, tmp_path):
        from multi_agent.graph import plan_node

        with patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"), \
             patch("multi_agent.graph.save_state_snapshot"), \
             patch("multi_agent.graph.load_contract") as mock_lc, \
             patch("multi_agent.graph.resolve_builder", return_value="ws"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="cursor"), \
             patch("multi_agent.graph.write_inbox"):
            mock_lc.return_value = MagicMock(
                quality_gates=[], preconditions=[],
                supported_agents=None, timeout_sec=600,
            )
            state = {
                "task_id": "task-integ-1",
                "requirement": "test",
                "skill_id": "code-implement",
                "done_criteria": ["test"],
                "timeout_sec": 600,
                "retry_budget": 2,
                "retry_count": 0,
                "input_payload": {},
                "builder_explicit": "",
                "reviewer_explicit": "",
                "conversation": [],
            }
            result = plan_node(state)
            assert "builder_id" in result
            assert "reviewer_id" in result


class TestBuildErrorFlow:
    """Task 46: builder returns error → failed."""

    def test_build_error_returns_failed(self, tmp_path):
        from multi_agent.graph import build_node

        error_output = {"status": "error", "summary": "crash"}

        with patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"), \
             patch("multi_agent.graph.save_state_snapshot"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.interrupt", return_value=error_output):
            state = {
                "task_id": "task-err",
                "requirement": "test",
                "skill_id": "code-implement",
                "done_criteria": ["test"],
                "timeout_sec": 600,
                "retry_budget": 2,
                "retry_count": 0,
                "started_at": 0,
                "build_started_at": None,
                "builder_id": "ws",
                "reviewer_id": "cursor",
                "conversation": [],
                "input_payload": {},
            }
            result = build_node(state)
            assert result.get("final_status") == "failed"


class TestRejectRetryFlow:
    """Task 46: plan → build → review(reject) → retry → approve."""

    def test_reject_then_approve(self):
        from multi_agent.graph import decide_node

        # First call: reject with budget remaining
        state_reject = {
            "task_id": "task-retry",
            "reviewer_output": {"decision": "reject", "feedback": "fix imports"},
            "retry_count": 0, "retry_budget": 2,
            "conversation": [], "done_criteria": ["works"],
            "builder_id": "ws", "reviewer_id": "cursor",
        }
        with patch("multi_agent.graph.write_dashboard"), \
             patch("multi_agent.graph.archive_conversation"):
            result = decide_node(state_reject)
        assert result["retry_count"] == 1
        assert result.get("final_status") is None  # not final yet

        # Second call: approve
        state_approve = {
            "task_id": "task-retry",
            "reviewer_output": {"decision": "approve", "feedback": "LGTM"},
            "retry_count": 1, "retry_budget": 2,
            "conversation": [], "done_criteria": ["works"],
            "builder_id": "ws", "reviewer_id": "cursor",
        }
        with patch("multi_agent.graph.write_dashboard"), \
             patch("multi_agent.graph.archive_conversation"):
            result = decide_node(state_approve)
        assert result["final_status"] == "approved"


class TestBudgetExhausted:
    """Task 46: retry budget exhausted → escalated."""

    def test_budget_exhausted_escalates(self):
        from multi_agent.graph import decide_node

        state = {
            "task_id": "task-budget",
            "reviewer_output": {"decision": "reject", "feedback": "still broken"},
            "retry_count": 2, "retry_budget": 2,
            "conversation": [], "done_criteria": ["works"],
            "builder_id": "ws", "reviewer_id": "cursor",
        }
        with patch("multi_agent.graph.write_dashboard"), \
             patch("multi_agent.graph.archive_conversation"):
            result = decide_node(state)
        assert result["final_status"] == "escalated"
        assert result["error"] == "BUDGET_EXHAUSTED"


class TestBuildTimeout:
    """Task 46: build timeout → failed."""

    def test_timeout_returns_failed(self):
        import time
        from multi_agent.graph import build_node

        state = {
            "task_id": "task-timeout",
            "skill_id": "code-implement",
            "builder_id": "ws", "reviewer_id": "cursor",
            "done_criteria": ["works"], "timeout_sec": 10,
            "started_at": time.time() - 100,  # started 100s ago
            "build_started_at": time.time() - 100,
            "conversation": [], "retry_budget": 2, "retry_count": 0,
        }
        with patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"), \
             patch("multi_agent.graph.save_state_snapshot"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.interrupt", return_value={"status": "completed", "summary": "ok"}):
            result = build_node(state)
        assert result["final_status"] == "failed"
        assert "timeout" in result.get("error", "").lower() or "超时" in result.get("error", "")


class TestDecomposeIntegration:
    """Task 47: decompose flow tests."""

    def test_aggregate_results_correct(self):
        from multi_agent.meta_graph import aggregate_results

        sub_results = [
            {"sub_id": "auth", "status": "approved", "summary": "done",
             "changed_files": ["a.py"], "retry_count": 0, "duration_sec": 60},
            {"sub_id": "db", "status": "approved", "summary": "done",
             "changed_files": ["b.py"], "retry_count": 1, "duration_sec": 120},
        ]
        agg = aggregate_results("task-parent", sub_results)
        assert agg["final_status"] == "approved"
        assert agg["total_sub_tasks"] == 2
        assert agg["completed"] == 2
        assert agg["total_retries"] == 1
        assert agg["total_duration_sec"] == 180

    def test_aggregate_with_failure(self):
        from multi_agent.meta_graph import aggregate_results

        sub_results = [
            {"sub_id": "auth", "status": "approved", "summary": "ok",
             "changed_files": [], "retry_count": 0, "duration_sec": 60},
            {"sub_id": "db", "status": "failed", "summary": "err",
             "changed_files": [], "retry_count": 2, "duration_sec": 30},
        ]
        agg = aggregate_results("task-parent", sub_results)
        assert agg["final_status"] == "failed"
        assert "db" in agg["failed"]
        assert agg["completed"] == 1

    def test_aggregate_with_estimated_vs_actual(self):
        from multi_agent.meta_graph import aggregate_results

        sub_results = [
            {"sub_id": "a", "status": "approved", "summary": "ok",
             "changed_files": [], "retry_count": 0, "duration_sec": 120,
             "estimated_minutes": 5},
            {"sub_id": "b", "status": "approved", "summary": "ok",
             "changed_files": [], "retry_count": 0, "duration_sec": 180,
             "estimated_minutes": 10},
        ]
        agg = aggregate_results("task-p", sub_results)
        assert agg["estimated_total_minutes"] == 15
        assert agg["actual_total_minutes"] == 5.0  # 300s / 60

    def test_build_sub_task_state_includes_parent(self):
        from multi_agent.meta_graph import build_sub_task_state
        from multi_agent.schema import SubTask

        st = SubTask(id="login", description="impl login", done_criteria=["login works"])
        result = build_sub_task_state(st, parent_task_id="task-auth")
        assert result["parent_task_id"] == "task-auth"
        assert "login" in result["task_id"]
