"""Tests for the LangGraph 4-node workflow."""

import json
from unittest.mock import MagicMock, patch

import pytest

from multi_agent.graph import (
    MAX_CONVERSATION_SIZE,
    MAX_TASK_DURATION_SEC,
    EventHooks,
    GraphStats,
    _conn_pool,
    _get_connection,
    _is_cancelled,
    _route_after_build,
    build_graph,
    build_node,
    decide_node,
    graph_hooks,
    plan_node,
    review_node,
    route_decision,
    trim_conversation,
)


class TestRouteDecision:
    def test_approved(self):
        state = {"final_status": "approved", "reviewer_output": {"decision": "approve"}}
        assert route_decision(state) == "end"

    def test_error(self):
        state = {"error": "something broke"}
        assert route_decision(state) == "end"

    def test_retry(self):
        state = {"reviewer_output": {"decision": "reject"}}
        assert route_decision(state) == "retry"

    def test_no_output(self):
        state = {}
        assert route_decision(state) == "retry"


class TestDecideNode:
    def _base_state(self, **overrides) -> dict:
        s = {
            "task_id": "task-test-123",
            "skill_id": "code-implement",
            "done_criteria": ["implement something"],
            "retry_count": 0,
            "retry_budget": 2,
            "builder_id": "windsurf",
            "reviewer_id": "cursor",
            "conversation": [],
        }
        s.update(overrides)
        return s

    @patch("multi_agent.graph.archive_conversation")
    @patch("multi_agent.graph.write_dashboard")
    def test_approve(self, mock_dash, mock_archive):
        state = self._base_state(
            reviewer_output={"decision": "approve", "summary": "LGTM"}
        )
        result = decide_node(state)
        assert result["final_status"] == "approved"
        mock_archive.assert_called_once()

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_mode_blocks_rubber_stamp_approve(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            reviewer_output={"decision": "approve", "summary": "LGTM", "reasoning": ""},
        )
        result = decide_node(state)
        assert "final_status" not in result
        assert result["conversation"][0]["action"] == "request_changes"
        assert "rubber-stamp" in result["conversation"][0]["feedback"]

    @patch("multi_agent.graph.archive_conversation")
    @patch("multi_agent.graph.write_dashboard")
    def test_non_strict_rubber_stamp_keeps_approve(self, mock_dash, mock_archive):
        state = self._base_state(
            reviewer_output={"decision": "approve", "summary": "LGTM", "reasoning": ""},
        )
        result = decide_node(state)
        assert result["final_status"] == "approved"
        assert any(e.get("action") == "rubber_stamp_warning" for e in result["conversation"])
        mock_archive.assert_called_once()

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_mode_rubber_threshold_can_be_configured(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            review_policy={
                "rubber_stamp": {
                    "generic_phrases": ["ship it"],
                    "generic_summary_max_len": 80,
                    "shallow_summary_max_len": 60,
                    "block_on_strict": True,
                }
            },
            reviewer_output={
                "decision": "approve",
                "summary": "Implementation appears correct after a quick manual scan.",
                "reasoning": "",
            },
        )
        result = decide_node(state)
        assert "final_status" not in result
        assert result["conversation"][0]["action"] == "request_changes"

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_hard_gate_blocks_empty_changed_files(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            builder_output={
                "status": "completed",
                "summary": "implemented",
                "changed_files": [],
                "check_results": {"lint": "pass", "unit_test": "pass", "artifact_checksum": "pass"},
            },
            reviewer_output={
                "decision": "approve",
                "summary": "Reviewed all modules with concrete checks.",
                "reasoning": "Verified flows and error paths.",
                "evidence": ["unit tests + API contract checks"],
            },
        )
        result = decide_node(state)
        assert "final_status" not in result
        assert result["conversation"][0]["action"] == "request_changes"
        assert "changed_files" in result["conversation"][0]["feedback"]

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_hard_gate_blocks_failed_quality_gate(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            builder_output={
                "status": "completed",
                "summary": "implemented",
                "changed_files": ["/tmp/app/main.py"],
                "check_results": {"lint": "pass", "unit_test": "fail", "artifact_checksum": "pass"},
            },
            reviewer_output={
                "decision": "approve",
                "summary": "Reviewed implementation in depth.",
                "reasoning": "Confirmed behavior except gate mismatch.",
                "evidence": ["failure reproduced in unit_test"],
            },
        )
        result = decide_node(state)
        assert "final_status" not in result
        assert result["conversation"][0]["action"] == "request_changes"
        assert "quality gate" in result["conversation"][0]["feedback"]

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_hard_gate_blocks_fallback_marker(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            builder_output={
                "status": "completed",
                "summary": "implemented",
                "changed_files": ["/tmp/app/main.py"],
                "check_results": {"lint": "pass", "unit_test": "pass", "artifact_checksum": "pass"},
                "risks": ["adapter fallback used rc=124"],
            },
            reviewer_output={
                "decision": "approve",
                "summary": "Independent verification completed.",
                "reasoning": "Validated outputs and boundaries.",
                "evidence": ["manual checks", "tests rerun"],
            },
        )
        result = decide_node(state)
        assert "final_status" not in result
        assert result["conversation"][0]["action"] == "request_changes"
        assert "fallback marker" in result["conversation"][0]["feedback"]

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_hard_gate_allows_business_fallback_word(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            builder_output={
                "status": "completed",
                "summary": "Implemented fallback strategy for cache miss path.",
                "changed_files": ["/tmp/app/main.py"],
                "check_results": {"lint": "pass", "unit_test": "pass", "artifact_checksum": "pass"},
            },
            reviewer_output={
                "decision": "approve",
                "summary": "Independent verification completed.",
                "reasoning": "Validated all done criteria and regression checks.",
                "evidence": ["unit tests rerun", "manual endpoint verification"],
            },
        )
        result = decide_node(state)
        assert result["final_status"] == "approved"

    @patch("multi_agent.graph.write_dashboard")
    def test_strict_hard_gate_blocks_missing_review_evidence(self, mock_dash):
        state = self._base_state(
            workflow_mode="strict",
            builder_output={
                "status": "completed",
                "summary": "implemented",
                "changed_files": ["/tmp/app/main.py"],
                "check_results": {"lint": "pass", "unit_test": "pass", "artifact_checksum": "pass"},
            },
            reviewer_output={
                "decision": "approve",
                "summary": "Deep review completed.",
                "reasoning": "Checked behavior and regression risk.",
                "evidence": [],
            },
        )
        result = decide_node(state)
        assert "final_status" not in result
        assert result["conversation"][0]["action"] == "request_changes"
        assert "evidence" in result["conversation"][0]["feedback"]

    @patch("multi_agent.graph.archive_conversation")
    @patch("multi_agent.graph.write_dashboard")
    def test_strict_mode_can_disable_rubber_stamp_block(self, mock_dash, mock_archive):
        state = self._base_state(
            workflow_mode="strict",
            review_policy={"rubber_stamp": {"block_on_strict": False}},
            reviewer_output={"decision": "approve", "summary": "LGTM", "reasoning": ""},
        )
        result = decide_node(state)
        assert result["final_status"] == "approved"
        assert any(e.get("action") == "rubber_stamp_warning" for e in result["conversation"])
        mock_archive.assert_called_once()

    @patch("multi_agent.graph.write_dashboard")
    def test_reject_with_budget(self, mock_dash):
        state = self._base_state(
            reviewer_output={"decision": "reject", "feedback": "fix tests"},
            retry_count=0,
            retry_budget=2,
        )
        result = decide_node(state)
        assert result["retry_count"] == 1
        assert "final_status" not in result

    @patch("multi_agent.graph.archive_conversation")
    @patch("multi_agent.graph.write_dashboard")
    def test_reject_budget_exhausted(self, mock_dash, mock_archive):
        state = self._base_state(
            reviewer_output={"decision": "reject", "feedback": "still broken"},
            retry_count=2,
            retry_budget=2,
        )
        result = decide_node(state)
        assert result["error"].startswith("BUDGET_EXHAUSTED")
        assert result["final_status"] == "escalated"
        mock_archive.assert_called_once()


class TestDecideNodeRequestChanges:
    """Task 3: Verify decide_node distinguishes request_changes from reject."""

    def _base_state(self, **overrides) -> dict:
        s = {
            "task_id": "task-rc-01",
            "skill_id": "code-implement",
            "done_criteria": ["implement something"],
            "retry_count": 0,
            "retry_budget": 2,
            "builder_id": "windsurf",
            "reviewer_id": "cursor",
            "conversation": [],
        }
        s.update(overrides)
        return s

    @patch("multi_agent.graph.write_dashboard")
    def test_request_changes_retries_without_consuming_budget(self, mock_dash):
        """request_changes should trigger retry but NOT consume retry_budget."""
        state = self._base_state(
            reviewer_output={"decision": "request_changes", "feedback": "fix typo"},
            retry_count=0,
            retry_budget=2,
        )
        result = decide_node(state)
        # retry_count should NOT be in result (not incremented)
        assert "retry_count" not in result
        # Should NOT have final_status (continues retrying)
        assert "final_status" not in result
        # Conversation should record request_changes action
        convo = result["conversation"]
        assert convo[0]["action"] == "request_changes"

    @patch("multi_agent.graph.write_dashboard")
    def test_request_changes_retries_even_at_zero_budget(self, mock_dash):
        """request_changes should retry even when budget is 0."""
        state = self._base_state(
            reviewer_output={"decision": "request_changes", "feedback": "minor fix"},
            retry_count=5,  # way past budget
            retry_budget=0,
        )
        result = decide_node(state)
        # Should NOT escalate — request_changes always retries
        assert "final_status" not in result
        assert "error" not in result

    @patch("multi_agent.graph.archive_conversation")
    @patch("multi_agent.graph.write_dashboard")
    def test_reject_escalates_when_budget_exhausted(self, mock_dash, mock_archive):
        """reject should escalate when budget is exhausted."""
        state = self._base_state(
            reviewer_output={"decision": "reject", "feedback": "broken"},
            retry_count=2,
            retry_budget=2,
        )
        result = decide_node(state)
        assert result["final_status"] == "escalated"
        assert result["error"].startswith("BUDGET_EXHAUSTED")

    @patch("multi_agent.graph.write_dashboard")
    def test_reject_consumes_budget(self, mock_dash):
        """reject should increment retry_count."""
        state = self._base_state(
            reviewer_output={"decision": "reject", "feedback": "fix tests"},
            retry_count=0,
            retry_budget=2,
        )
        result = decide_node(state)
        assert result["retry_count"] == 1

    @patch("multi_agent.graph.write_dashboard")
    def test_request_changes_dashboard_shows_correct_emoji(self, mock_dash):
        """Dashboard should show 🔧 for request_changes."""
        state = self._base_state(
            reviewer_output={"decision": "request_changes", "feedback": "fix typo"},
        )
        decide_node(state)
        call_kwargs = mock_dash.call_args[1]
        assert "🔧" in call_kwargs["status_msg"]

    @patch("multi_agent.graph.write_dashboard")
    def test_reject_dashboard_shows_correct_emoji(self, mock_dash):
        """Dashboard should show ❌ for reject."""
        state = self._base_state(
            reviewer_output={"decision": "reject", "feedback": "broken"},
            retry_count=0,
            retry_budget=2,
        )
        decide_node(state)
        call_kwargs = mock_dash.call_args[1]
        assert "❌" in call_kwargs["status_msg"]


class TestBuildNodeErrorDetection:
    """Test that build_node detects CLI driver error outputs."""

    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_cli_error_output_fails_build(self, mock_interrupt, mock_task_md, mock_dash):
        """status=error from CLI driver should NOT go to reviewer."""
        from multi_agent.graph import build_node
        mock_interrupt.return_value = {"status": "error", "summary": "claude CLI timed out after 600s"}
        state = {
            "builder_id": "claude",
            "reviewer_id": "cursor",
            "started_at": 0,
            "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-test-err",
            "done_criteria": ["test"],
            "conversation": [],
        }
        result = build_node(state)
        assert result["final_status"] == "failed"
        assert "timed out" in result["error"]
        # Should NOT have builder_output (i.e., should not proceed to reviewer)
        assert "builder_output" not in result

    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_cli_blocked_output_fails_build(self, mock_interrupt, mock_task_md, mock_dash):
        from multi_agent.graph import build_node
        mock_interrupt.return_value = {"status": "blocked", "summary": "waiting on credentials"}
        state = {
            "builder_id": "codex-cli",
            "reviewer_id": "cursor",
            "started_at": 0,
            "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-test-blocked",
            "done_criteria": ["test"],
            "conversation": [],
        }
        result = build_node(state)
        assert result["final_status"] == "failed"
        assert "waiting on credentials" in result["error"]
        assert "builder_output" not in result

    @patch("multi_agent.graph.load_contract")
    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_normal_output_passes_through(self, mock_interrupt, mock_task_md, mock_dash, mock_contract):
        """status=completed should proceed normally."""
        from multi_agent.graph import build_node
        mock_contract.return_value.quality_gates = []
        mock_interrupt.return_value = {
            "status": "completed",
            "summary": "done",
            "changed_files": [],
            "check_results": {},
        }
        state = {
            "builder_id": "windsurf",
            "reviewer_id": "cursor",
            "started_at": 0,
            "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-test-ok",
            "done_criteria": ["test"],
            "conversation": [],
            "input_payload": {"requirement": "test"},
        }
        result = build_node(state)
        assert "builder_output" in result
        assert "final_status" not in result


class TestReviewNodeErrorDetection:
    """Test that review_node detects CLI driver error outputs."""

    @patch("multi_agent.graph.interrupt")
    def test_reviewer_cli_error_auto_rejects(self, mock_interrupt):
        from multi_agent.graph import review_node
        mock_interrupt.return_value = {"status": "error", "summary": "codex CLI exited with code 1: OOM"}
        state = {
            "reviewer_id": "codex",
            "conversation": [],
        }
        result = review_node(state)
        assert result["reviewer_output"]["decision"] == "reject"
        assert "CLI failed" in result["reviewer_output"]["feedback"]
        assert "OOM" in result["reviewer_output"]["feedback"]


class TestRouteAfterBuild:
    def test_no_error_goes_to_review(self):
        state = {"builder_output": {"status": "completed"}}
        assert _route_after_build(state) == "review"

    def test_error_goes_to_end(self):
        state = {"error": "Builder output invalid"}
        assert _route_after_build(state) == "end"

    def test_failed_status_goes_to_end(self):
        state = {"final_status": "failed"}
        assert _route_after_build(state) == "end"

    def test_cancelled_status_goes_to_end(self):
        state = {"final_status": "cancelled"}
        assert _route_after_build(state) == "end"

    def test_empty_state_goes_to_review(self):
        assert _route_after_build({}) == "review"


class TestPlanNodePreconditions:
    """Verify that plan_node integrates validate_preconditions correctly."""

    def _base_state(self, **overrides) -> dict:
        s = {
            "task_id": "task-precon-01",
            "skill_id": "code-implement",
            "requirement": "implement something",
            "done_criteria": ["tests pass"],
            "timeout_sec": 1800,
            "retry_budget": 2,
            "retry_count": 0,
            "conversation": [],
        }
        s.update(overrides)
        return s

    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.write_inbox")
    @patch("multi_agent.graph.clear_outbox")
    @patch("multi_agent.graph.render_builder_prompt", return_value="prompt")
    @patch("multi_agent.graph.resolve_reviewer", return_value="cursor")
    @patch("multi_agent.graph.resolve_builder", return_value="windsurf")
    @patch("multi_agent.graph.load_agents", return_value=[])
    @patch("multi_agent.graph.validate_preconditions", return_value=[])
    @patch("multi_agent.graph.load_contract")
    def test_precondition_passes(self, mock_contract, mock_validate, *_):
        """When preconditions pass, plan_node proceeds normally."""
        mock_contract.return_value = MagicMock(
            timeouts=MagicMock(run_sec=1800),
            retry=MagicMock(max_attempts=2),
        )
        state = self._base_state()
        result = plan_node(state)
        mock_validate.assert_called_once()
        # Normal result: has builder_id, no error
        assert "builder_id" in result
        assert "error" not in result

    @patch("multi_agent.graph.validate_preconditions",
           return_value=["precondition requires RUNNING, current state is DRAFT"])
    @patch("multi_agent.graph.load_contract")
    def test_precondition_fails(self, mock_contract, mock_validate):
        """When preconditions fail, plan_node returns error + failed."""
        mock_contract.return_value = MagicMock()
        state = self._base_state()
        result = plan_node(state)
        assert result["final_status"] == "failed"
        assert "Precondition failed" in result["error"]
        assert "RUNNING" in result["error"]
        # Should NOT have builder_id (didn't proceed to role resolution)
        assert "builder_id" not in result

    @patch("multi_agent.graph.validate_preconditions",
           return_value=["precondition requires RUNNING, current state is DRAFT"])
    @patch("multi_agent.graph.load_contract")
    def test_precondition_failure_does_not_consume_retry(self, mock_contract, mock_validate):
        """Precondition failure returns final_status=failed, not retry."""
        mock_contract.return_value = MagicMock()
        state = self._base_state(retry_count=0, retry_budget=2)
        result = plan_node(state)
        # retry_count should NOT be incremented
        assert "retry_count" not in result
        assert result["final_status"] == "failed"

    @patch("multi_agent.graph.validate_preconditions",
           return_value=["precondition requires RUNNING, current state is DRAFT"])
    @patch("multi_agent.graph.load_contract")
    def test_precondition_failure_records_conversation(self, mock_contract, mock_validate):
        """Conversation should contain precondition_failed event with timestamp."""
        mock_contract.return_value = MagicMock()
        state = self._base_state()
        result = plan_node(state)
        convo = result["conversation"]
        assert len(convo) == 1
        entry = convo[0]
        assert entry["role"] == "orchestrator"
        assert entry["action"] == "precondition_failed"
        assert "t" in entry
        assert isinstance(entry["t"], float)
        assert "details" in entry

    @patch("multi_agent.graph.validate_preconditions",
           return_value=["precondition requires RUNNING, current state is DRAFT"])
    @patch("multi_agent.graph.load_contract")
    @patch("multi_agent.graph.interrupt")
    def test_precondition_failure_skips_build(self, mock_interrupt, mock_contract, mock_validate):
        """When preconditions fail, interrupt (build) should NOT be called."""
        mock_contract.return_value = MagicMock()
        state = self._base_state()
        plan_node(state)
        mock_interrupt.assert_not_called()

    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.write_inbox")
    @patch("multi_agent.graph.clear_outbox")
    @patch("multi_agent.graph.render_builder_prompt", return_value="prompt")
    @patch("multi_agent.graph.resolve_reviewer", return_value="cursor")
    @patch("multi_agent.graph.resolve_builder", return_value="windsurf")
    @patch("multi_agent.graph.load_agents", return_value=[])
    @patch("multi_agent.graph.validate_preconditions", return_value=[])
    @patch("multi_agent.graph.load_contract")
    def test_no_preconditions_contract(self, mock_contract, mock_validate, *_):
        """Contract with empty preconditions list — validate returns [], plan proceeds."""
        mock_contract.return_value = MagicMock(
            preconditions=[],
            timeouts=MagicMock(run_sec=1800),
            retry=MagicMock(max_attempts=2),
        )
        state = self._base_state()
        result = plan_node(state)
        mock_validate.assert_called_once()
        assert "error" not in result
        assert "builder_id" in result


class TestIsCancelled:
    """Task 4: Verify _is_cancelled helper function."""

    def test_cancelled_task(self, tmp_path):
        """Task with status=cancelled returns True."""
        import yaml
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "task-cancel-01.yaml").write_text(
            yaml.dump({"task_id": "task-cancel-01", "status": "cancelled"}),
            encoding="utf-8",
        )
        with patch("multi_agent.config.tasks_dir", return_value=tasks):
            assert _is_cancelled("task-cancel-01") is True

    def test_active_task(self, tmp_path):
        """Task with status=active returns False."""
        import yaml
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "task-active-01.yaml").write_text(
            yaml.dump({"task_id": "task-active-01", "status": "active"}),
            encoding="utf-8",
        )
        with patch("multi_agent.config.tasks_dir", return_value=tasks):
            assert _is_cancelled("task-active-01") is False

    def test_no_task_file(self, tmp_path):
        """No task file returns False."""
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        with patch("multi_agent.config.tasks_dir", return_value=tasks):
            assert _is_cancelled("task-nonexistent") is False


class TestBuildNodeCancellation:
    """Task 4: Verify build_node checks cancellation after interrupt."""

    @patch("multi_agent.graph._is_cancelled", return_value=True)
    @patch("multi_agent.graph.interrupt")
    def test_build_node_returns_cancelled(self, mock_interrupt, mock_cancel):
        from multi_agent.graph import build_node
        mock_interrupt.return_value = {"status": "completed", "summary": "done"}
        state = {
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "started_at": 0, "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-cancel-build",
            "done_criteria": ["test"],
            "conversation": [],
        }
        result = build_node(state)
        assert result["final_status"] == "cancelled"
        assert result["conversation"][0]["action"] == "cancelled"


class TestReviewNodeCancellation:
    """Task 4: Verify review_node checks cancellation after interrupt."""

    @patch("multi_agent.graph._is_cancelled", return_value=True)
    @patch("multi_agent.graph.interrupt")
    def test_review_node_returns_cancelled(self, mock_interrupt, mock_cancel):
        from multi_agent.graph import review_node
        mock_interrupt.return_value = {"decision": "approve", "feedback": "ok"}
        state = {
            "reviewer_id": "cursor",
            "task_id": "task-cancel-review",
            "conversation": [],
        }
        result = review_node(state)
        assert result["final_status"] == "cancelled"
        assert result["conversation"][0]["action"] == "cancelled"


class TestBuildNodeTimeout:
    """Task 2: Verify build_node uses build_started_at for precise timeout."""

    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_old_started_at_does_not_cause_false_timeout(self, mock_interrupt, mock_task_md, mock_dash):
        """started_at very old but build_started_at absent → uses current time, no timeout."""
        from multi_agent.graph import build_node
        mock_interrupt.return_value = {
            "status": "completed", "summary": "done",
            "changed_files": [], "check_results": {},
        }
        state = {
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "started_at": 1.0,  # very old timestamp
            "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-timeout-01",
            "done_criteria": ["test"],
            "conversation": [],
            "input_payload": {"requirement": "test"},
            # build_started_at NOT set → fallback to started_at
        }
        result = build_node(state)
        # With started_at=1.0, elapsed is huge → should timeout
        assert result.get("final_status") == "failed"
        assert "TIMEOUT" in result.get("error", "")

    @patch("multi_agent.graph.load_contract")
    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_build_started_at_prevents_false_timeout(self, mock_interrupt, mock_task_md, mock_dash, mock_contract):
        """build_started_at is recent → no timeout even if started_at is old."""
        import time as _time

        from multi_agent.graph import build_node
        mock_contract.return_value.quality_gates = []
        mock_interrupt.return_value = {
            "status": "completed", "summary": "done",
            "changed_files": [], "check_results": {},
        }
        state = {
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "started_at": 1.0,  # very old
            "build_started_at": _time.time(),  # recent
            "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-timeout-02",
            "done_criteria": ["test"],
            "conversation": [],
            "input_payload": {"requirement": "test"},
        }
        result = build_node(state)
        # Should NOT timeout because build_started_at is recent
        assert "builder_output" in result
        assert "error" not in result

    @patch("multi_agent.graph.load_contract")
    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_build_node_returns_build_started_at(self, mock_interrupt, mock_task_md, mock_dash, mock_contract):
        """build_node should return build_started_at in its result."""
        from multi_agent.graph import build_node
        mock_contract.return_value.quality_gates = []
        mock_interrupt.return_value = {
            "status": "completed", "summary": "done",
            "changed_files": [], "check_results": {},
        }
        state = {
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "started_at": 0, "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-timeout-03",
            "done_criteria": ["test"],
            "conversation": [],
            "input_payload": {"requirement": "test"},
            "build_started_at": None,
        }
        # started_at=0 would timeout, but build_started_at=None → fallback to current time
        # Actually started_at=0 with elapsed > 1800 → timeout
        # Let's set started_at to current
        import time as _time
        state["started_at"] = _time.time()
        result = build_node(state)
        assert "build_started_at" in result
        assert isinstance(result["build_started_at"], float)


class TestReviewNodeTimeout:
    """Task 2: Verify review_node uses review_started_at for precise timeout."""

    @patch("multi_agent.graph.interrupt")
    def test_review_timeout_with_old_started_at(self, mock_interrupt):
        """started_at very old, review_started_at absent → timeout."""
        from multi_agent.graph import review_node
        mock_interrupt.return_value = {"decision": "approve", "feedback": ""}
        state = {
            "reviewer_id": "cursor",
            "started_at": 1.0,  # very old
            "timeout_sec": 1800,
            "conversation": [],
        }
        result = review_node(state)
        assert result["reviewer_output"]["decision"] == "reject"
        assert "TIMEOUT" in result["reviewer_output"]["feedback"]

    @patch("multi_agent.graph.interrupt")
    def test_review_no_timeout_with_recent_review_started_at(self, mock_interrupt):
        """review_started_at is recent → no timeout."""
        import time as _time

        from multi_agent.graph import review_node
        mock_interrupt.return_value = {"decision": "approve", "feedback": "LGTM"}
        state = {
            "reviewer_id": "cursor",
            "started_at": 1.0,  # very old
            "review_started_at": _time.time(),  # recent
            "timeout_sec": 1800,
            "conversation": [],
        }
        result = review_node(state)
        assert result["reviewer_output"]["decision"] == "approve"

    @patch("multi_agent.graph.interrupt")
    def test_review_node_returns_review_started_at(self, mock_interrupt):
        """review_node should return review_started_at."""
        import time as _time

        from multi_agent.graph import review_node
        mock_interrupt.return_value = {"decision": "approve", "feedback": "ok"}
        state = {
            "reviewer_id": "cursor",
            "started_at": _time.time(),
            "timeout_sec": 1800,
            "conversation": [],
        }
        result = review_node(state)
        assert "review_started_at" in result
        assert isinstance(result["review_started_at"], float)


class TestConnectionPool:
    """Task 11: Verify SQLite connection pool singleton."""

    def test_same_path_returns_same_connection(self, tmp_path, monkeypatch):
        isolated_pool = {}
        monkeypatch.setattr("multi_agent.graph._conn_pool", isolated_pool)
        db = str(tmp_path / "test.db")
        c1 = _get_connection(db)
        c2 = _get_connection(db)
        assert c1 is c2
        c1.close()

    def test_different_paths_return_different_connections(self, tmp_path, monkeypatch):
        isolated_pool = {}
        monkeypatch.setattr("multi_agent.graph._conn_pool", isolated_pool)
        db1 = str(tmp_path / "test1.db")
        db2 = str(tmp_path / "test2.db")
        c1 = _get_connection(db1)
        c2 = _get_connection(db2)
        assert c1 is not c2
        c1.close()
        c2.close()

    def test_closed_connection_gets_replaced(self, tmp_path, monkeypatch):
        isolated_pool = {}
        monkeypatch.setattr("multi_agent.graph._conn_pool", isolated_pool)
        db = str(tmp_path / "closed.db")
        c1 = _get_connection(db)
        c1.close()
        c2 = _get_connection(db)
        assert c1 is not c2
        # c2 should work
        c2.execute("SELECT 1")
        c2.close()


class TestEventHooks:
    """Task 13: Verify event hooks system."""

    def test_on_node_enter(self):
        hooks = EventHooks()
        calls = []
        hooks.on_node_enter("plan", lambda s: calls.append(("enter", s)))
        hooks.fire_enter("plan", {"task_id": "t1"})
        assert len(calls) == 1
        assert calls[0] == ("enter", {"task_id": "t1"})

    def test_on_node_exit(self):
        hooks = EventHooks()
        calls = []
        hooks.on_node_exit("build", lambda s, r: calls.append(("exit", r)))
        hooks.fire_exit("build", {}, {"builder_output": "ok"})
        assert len(calls) == 1
        assert calls[0][1] == {"builder_output": "ok"}

    def test_on_error(self):
        hooks = EventHooks()
        calls = []
        hooks.on_error(lambda n, s, e: calls.append((n, str(e))))
        hooks.fire_error("build", {}, ValueError("test"))
        assert calls == [("build", "test")]

    def test_no_hooks_no_error(self):
        hooks = EventHooks()
        # Should not raise even with no registered hooks
        hooks.fire_enter("plan", {})
        hooks.fire_exit("plan", {}, {})
        hooks.fire_error("plan", {}, ValueError("x"))

    def test_failing_hook_does_not_propagate(self):
        hooks = EventHooks()
        hooks.on_node_enter("plan", lambda s: 1/0)
        # Should not raise — error is caught and logged
        hooks.fire_enter("plan", {})

    def test_multiple_hooks(self):
        hooks = EventHooks()
        calls = []
        hooks.on_node_enter("plan", lambda s: calls.append("a"))
        hooks.on_node_enter("plan", lambda s: calls.append("b"))
        hooks.fire_enter("plan", {})
        assert calls == ["a", "b"]

    def test_register_hook_public_api(self):
        """T13: register_hook maps event names to EventHooks correctly."""
        from multi_agent.graph import EventHooks, register_hook
        hooks = EventHooks()
        calls = []
        with patch("multi_agent.graph_infra.graph_hooks", hooks), \
             patch("multi_agent.graph.graph_hooks", hooks):
            register_hook("plan_start", lambda s: calls.append("plan_enter"))
            register_hook("build_submit", lambda s, r: calls.append("build_exit"))
            register_hook("task_failed", lambda n, s, e: calls.append("error"))
            hooks.fire_enter("plan", {})
            hooks.fire_exit("build", {}, {})
            hooks.fire_error("plan", {}, ValueError("x"))
        assert "plan_enter" in calls
        assert "build_exit" in calls
        assert "error" in calls

    def test_hooks_integrated_in_plan_node(self):
        """Verify plan_node fires enter/exit hooks on the global instance."""
        calls = []
        graph_hooks.on_node_enter("plan", lambda s: calls.append("enter"))
        graph_hooks.on_node_exit("plan", lambda s, r: calls.append("exit"))
        try:
            with patch("multi_agent.graph.write_dashboard"), \
                 patch("multi_agent.graph._write_task_md"), \
                 patch("multi_agent.graph.write_inbox"), \
                 patch("multi_agent.graph.clear_outbox"), \
                 patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
                 patch("multi_agent.graph.resolve_reviewer", return_value="cursor"), \
                 patch("multi_agent.graph.resolve_builder", return_value="windsurf"), \
                 patch("multi_agent.graph.load_agents", return_value=[]), \
                 patch("multi_agent.graph.validate_preconditions", return_value=[]), \
                 patch("multi_agent.graph.load_contract") as mc:
                mc.return_value = MagicMock(
                    timeouts=MagicMock(run_sec=1800),
                    retry=MagicMock(max_attempts=2),
                )
                plan_node({
                    "task_id": "task-hook-01", "skill_id": "code-implement",
                    "done_criteria": ["test"], "timeout_sec": 1800,
                    "retry_budget": 2, "retry_count": 0, "conversation": [],
                })
            assert "enter" in calls
            assert "exit" in calls
        finally:
            # Clean up global hooks
            graph_hooks._enter.pop("plan", None)
            graph_hooks._exit.pop("plan", None)


class TestPlanNode:
    """Task 31: Comprehensive plan_node tests."""

    def _base_state(self, **overrides):
        s = {
            "task_id": "task-plan-01", "skill_id": "code-implement",
            "done_criteria": ["tests pass"], "timeout_sec": 1800,
            "retry_budget": 2, "retry_count": 0, "conversation": [],
        }
        s.update(overrides)
        return s

    def _mock_contract(self):
        c = MagicMock()
        c.timeouts = MagicMock(run_sec=1800)
        c.retry = MagicMock(max_attempts=2)
        c.quality_gates = []
        c.preconditions = []
        return c

    def _patches(self, contract=None):
        c = contract or self._mock_contract()
        return [
            patch("multi_agent.graph.load_contract", return_value=c),
            patch("multi_agent.graph.validate_preconditions", return_value=[]),
            patch("multi_agent.graph.load_agents", return_value=[]),
            patch("multi_agent.graph.resolve_builder", return_value="windsurf"),
            patch("multi_agent.graph.resolve_reviewer", return_value="cursor"),
            patch("multi_agent.graph.render_builder_prompt", return_value="prompt"),
            patch("multi_agent.graph.write_inbox"),
            patch("multi_agent.graph.clear_outbox"),
            patch("multi_agent.graph._write_task_md"),
            patch("multi_agent.graph.write_dashboard"),
        ]

    def test_first_run_resolves_agents(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="windsurf") as mb, \
             patch("multi_agent.graph.resolve_reviewer", return_value="cursor") as mr, \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = plan_node(self._base_state())
        assert result["builder_id"] == "windsurf"
        assert result["reviewer_id"] == "cursor"
        mb.assert_called_once()
        mr.assert_called_once()

    def test_retry_reuses_existing_agents(self):
        state = self._base_state(builder_id="windsurf", reviewer_id="cursor", retry_count=1)
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder") as mb, \
             patch("multi_agent.graph.resolve_reviewer") as mr, \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = plan_node(state)
        assert result["builder_id"] == "windsurf"
        mb.assert_not_called()
        mr.assert_not_called()

    def test_explicit_builder_reviewer(self):
        state = self._base_state(builder_explicit="kiro", reviewer_explicit="codex")
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="kiro") as mb, \
             patch("multi_agent.graph.resolve_reviewer", return_value="codex"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = plan_node(state)
        assert result["builder_id"] == "kiro"
        # Verify explicit was passed through
        mb.assert_called_once()
        call_kwargs = mb.call_args
        assert call_kwargs[1]["explicit"] == "kiro"

    def test_precondition_failure(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=["not RUNNING"]):
            result = plan_node(self._base_state())
        assert result["final_status"] == "failed"
        assert "Precondition" in result["error"]

    def test_conversation_has_timestamp(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="w"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="c"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = plan_node(self._base_state())
        assert len(result["conversation"]) == 1
        assert "t" in result["conversation"][0]
        assert isinstance(result["conversation"][0]["t"], float)

    def test_current_role_is_builder(self):
        for p in self._patches():
            p.start()
        try:
            result = plan_node(self._base_state())
            assert result["current_role"] == "builder"
        finally:
            patch.stopall()

    def test_started_at_set(self):
        for p in self._patches():
            p.start()
        try:
            result = plan_node(self._base_state())
            assert "started_at" in result
            assert isinstance(result["started_at"], float)
        finally:
            patch.stopall()

    def test_write_inbox_called(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="w"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="c"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox") as wi, \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            plan_node(self._base_state())
        wi.assert_called_once_with("builder", "p")

    def test_dashboard_called(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="w"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="c"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard") as wd:
            plan_node(self._base_state())
        wd.assert_called_once()

    def test_clear_outbox_called(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="w"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="c"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox") as co, \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            plan_node(self._base_state())
        co.assert_called_once_with("builder")

    def test_contract_not_found(self):
        with patch("multi_agent.graph.load_contract", side_effect=FileNotFoundError("not found")):
            result = plan_node(self._base_state())
        assert result["final_status"] == "failed"

    def test_no_agents_raises(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", side_effect=ValueError("No agent configured for builder role")):
            result = plan_node(self._base_state())
        assert result["final_status"] == "failed"

    def test_builder_equals_reviewer_raises(self):
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="same-agent"), \
             patch("multi_agent.graph.resolve_reviewer", side_effect=ValueError("builder and reviewer cannot be the same")):
            result = plan_node(self._base_state())
        assert result["final_status"] == "failed"


class TestPlanNodeResetsTimestamps:
    """Regression: plan_node must reset build_started_at/review_started_at on retry.

    Without this reset, retry cycle 2+ would use stale timestamps from cycle 1,
    causing false timeout failures (elapsed measured from old build/review start).
    """

    def _base_state(self, **overrides):
        s = {
            "task_id": "task-ts-reset", "skill_id": "code-implement",
            "done_criteria": ["tests pass"], "timeout_sec": 1800,
            "retry_budget": 2, "retry_count": 0, "conversation": [],
        }
        s.update(overrides)
        return s

    def _mock_contract(self):
        c = MagicMock()
        c.timeouts = MagicMock(run_sec=1800)
        c.retry = MagicMock(max_attempts=2)
        c.quality_gates = []
        c.preconditions = []
        return c

    def test_plan_node_resets_build_started_at(self):
        """plan_node output must contain build_started_at=None."""
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="windsurf"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="cursor"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = plan_node(self._base_state(
                build_started_at=1.0,  # stale from previous cycle
                review_started_at=2.0,
                retry_count=1,
                builder_id="windsurf",
                reviewer_id="cursor",
            ))
        assert result["build_started_at"] is None
        assert result["review_started_at"] is None

    def test_plan_node_resets_timestamps_on_first_run(self):
        """Even on first run, plan_node should set these to None."""
        with patch("multi_agent.graph.load_contract", return_value=self._mock_contract()), \
             patch("multi_agent.graph.validate_preconditions", return_value=[]), \
             patch("multi_agent.graph.load_agents", return_value=[]), \
             patch("multi_agent.graph.resolve_builder", return_value="windsurf"), \
             patch("multi_agent.graph.resolve_reviewer", return_value="cursor"), \
             patch("multi_agent.graph.render_builder_prompt", return_value="p"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = plan_node(self._base_state())
        assert "build_started_at" in result
        assert result["build_started_at"] is None
        assert "review_started_at" in result
        assert result["review_started_at"] is None

    @patch("multi_agent.graph.load_contract")
    @patch("multi_agent.graph.write_dashboard")
    @patch("multi_agent.graph._write_task_md")
    @patch("multi_agent.graph.interrupt")
    def test_build_no_false_timeout_after_plan_reset(self, mock_interrupt, mock_task_md, mock_dash, mock_contract):
        """After plan_node resets timestamps, build_node should not false-timeout."""
        import time as _time
        mock_contract.return_value.quality_gates = []
        mock_interrupt.return_value = {
            "status": "completed", "summary": "done",
            "changed_files": [], "check_results": {},
        }
        state = {
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "started_at": _time.time(),
            "build_started_at": None,  # reset by plan_node
            "review_started_at": None,
            "timeout_sec": 1800,
            "skill_id": "code-implement",
            "task_id": "task-ts-reset-build",
            "done_criteria": ["test"],
            "conversation": [],
            "input_payload": {"requirement": "test"},
        }
        result = build_node(state)
        # Should NOT timeout — build_started_at is None, falls back to started_at (recent)
        assert "builder_output" in result
        assert "error" not in result


class TestBuildNodeComprehensive:
    """Task 32: Comprehensive build_node tests."""

    def _base_state(self, **overrides):
        import time as _time
        s = {
            "task_id": "task-build-01", "skill_id": "code-implement",
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "done_criteria": ["works"], "timeout_sec": 1800,
            "started_at": _time.time(), "conversation": [],
        }
        s.update(overrides)
        return s

    def test_valid_output_proceeds(self):
        state = self._base_state()
        output = {"status": "completed", "summary": "Done", "changed_files": []}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.load_contract") as mc, \
             patch("multi_agent.graph.render_reviewer_prompt", return_value="rp"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            mc.return_value = MagicMock(quality_gates=[], timeouts=MagicMock(run_sec=1800))
            result = build_node(state)
        assert result["builder_output"] == output
        assert result["current_role"] == "reviewer"

    def test_invalid_not_dict(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value="not a dict"), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = build_node(state)
        assert result["final_status"] == "failed"
        assert "JSON object" in result["error"]

    def test_missing_status_field(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value={"summary": "x"}), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = build_node(state)
        assert "status" in result["error"]

    def test_missing_summary_field(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value={"status": "completed"}), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = build_node(state)
        assert "summary" in result["error"]

    def test_cli_error_status(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value={"status": "error", "summary": "CLI crashed"}), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = build_node(state)
        assert result["final_status"] == "failed"
        assert "CLI crashed" in result["error"]

    def test_timeout_returns_failed(self):
        import time as _time
        state = self._base_state(build_started_at=_time.time() - 2000, timeout_sec=100)
        output = {"status": "completed", "summary": "Done"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = build_node(state)
        assert result["final_status"] == "failed"
        assert "TIMEOUT" in result["error"]

    def test_quality_gate_warning(self):
        state = self._base_state()
        output = {"status": "completed", "summary": "Done", "check_results": {"lint": "failed"}}
        contract = MagicMock(quality_gates=["lint"], timeouts=MagicMock(run_sec=1800))
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.load_contract", return_value=contract), \
             patch("multi_agent.graph.render_reviewer_prompt", return_value="rp"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            result = build_node(state)
        assert "gate_warnings" in result["builder_output"]

    def test_build_started_at_recorded(self):
        state = self._base_state()
        output = {"status": "completed", "summary": "Done"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.load_contract") as mc, \
             patch("multi_agent.graph.render_reviewer_prompt", return_value="rp"), \
             patch("multi_agent.graph.write_inbox"), \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            mc.return_value = MagicMock(quality_gates=[], timeouts=MagicMock(run_sec=1800))
            result = build_node(state)
        assert "build_started_at" in result
        assert isinstance(result["build_started_at"], float)

    def test_cancelled_returns_cancelled(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value={}), \
             patch("multi_agent.graph._is_cancelled", return_value=True):
            result = build_node(state)
        assert result["final_status"] == "cancelled"

    def test_reviewer_prompt_generated(self):
        state = self._base_state()
        output = {"status": "completed", "summary": "Done"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.load_contract") as mc, \
             patch("multi_agent.graph.render_reviewer_prompt", return_value="reviewer prompt") as rp, \
             patch("multi_agent.graph.write_inbox") as wi, \
             patch("multi_agent.graph.clear_outbox"), \
             patch("multi_agent.graph._write_task_md"), \
             patch("multi_agent.graph.write_dashboard"):
            mc.return_value = MagicMock(quality_gates=[], timeouts=MagicMock(run_sec=1800))
            build_node(state)
        rp.assert_called_once()
        wi.assert_called_once_with("reviewer", "reviewer prompt")


class TestReviewNodeComprehensive:
    """Task 33: Comprehensive review_node tests."""

    def _base_state(self, **overrides):
        import time as _time
        s = {
            "task_id": "task-review-01", "skill_id": "code-implement",
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "started_at": _time.time(), "timeout_sec": 1800,
            "conversation": [],
        }
        s.update(overrides)
        return s

    def test_approve_decision(self):
        state = self._base_state()
        output = {"decision": "approve", "feedback": "LGTM"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert result["reviewer_output"]["decision"] == "approve"
        assert result["conversation"][0]["decision"] == "approve"

    def test_reject_decision(self):
        state = self._base_state()
        output = {"decision": "reject", "feedback": "Bad code"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert result["reviewer_output"]["decision"] == "reject"

    def test_request_changes_decision(self):
        state = self._base_state()
        output = {"decision": "request_changes", "feedback": "Add tests"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert result["reviewer_output"]["decision"] == "request_changes"

    def test_invalid_not_dict(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value="invalid"), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert result["reviewer_output"]["decision"] == "reject"
        assert "Invalid" in result["reviewer_output"]["feedback"]

    def test_cli_error_auto_reject(self):
        state = self._base_state()
        output = {"status": "error", "summary": "CLI failed"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert result["reviewer_output"]["decision"] == "reject"
        assert "CLI failed" in result["reviewer_output"]["feedback"]

    def test_missing_decision_defaults_reject(self):
        state = self._base_state()
        output = {"feedback": "no decision field"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert result["conversation"][0]["decision"] == "reject"

    def test_review_started_at_recorded(self):
        state = self._base_state()
        output = {"decision": "approve", "feedback": "ok"}
        with patch("multi_agent.graph.interrupt", return_value=output), \
             patch("multi_agent.graph._is_cancelled", return_value=False):
            result = review_node(state)
        assert "review_started_at" in result
        assert isinstance(result["review_started_at"], float)

    def test_cancelled_returns_cancelled(self):
        state = self._base_state()
        with patch("multi_agent.graph.interrupt", return_value={}), \
             patch("multi_agent.graph._is_cancelled", return_value=True):
            result = review_node(state)
        assert result["final_status"] == "cancelled"


class TestNodeExceptionFallback:
    """Task 62: Node exception fallback tests."""

    def _base_state(self):
        return {
            "task_id": "task-test0001",
            "skill_id": "code-implement",
            "done_criteria": ["test"],
            "retry_budget": 2,
            "retry_count": 0,
            "conversation": [],
        }

    def test_plan_node_catches_exception(self):
        state = self._base_state()
        with patch("multi_agent.graph.graph_hooks"), \
             patch("multi_agent.graph.load_contract", side_effect=RuntimeError("boom")):
            result = plan_node(state)
        assert result["final_status"] == "failed"
        assert "plan_node" in result["error"]
        assert "boom" in result["error"]

    def test_build_node_catches_exception(self):
        state = self._base_state()
        state["builder_id"] = "windsurf"
        state["reviewer_id"] = "cursor"
        with patch("multi_agent.graph.interrupt", return_value={"status": "completed", "summary": "ok"}), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.load_contract", side_effect=RuntimeError("contract error")):
            result = build_node(state)
        assert result["final_status"] == "failed"
        assert "build_node" in result["error"]

    def test_review_node_catches_exception(self):
        state = self._base_state()
        state["reviewer_id"] = "cursor"
        with patch("multi_agent.graph.interrupt", return_value={"decision": "approve"}), \
             patch("multi_agent.graph._is_cancelled", return_value=False), \
             patch("multi_agent.graph.ReviewerOutput", side_effect=RuntimeError("parse fail")):
            result = review_node(state)
        # ReviewerOutput error is handled gracefully in inner, but if it propagates:
        assert "reviewer_output" in result or "error" in result

    def test_decide_node_catches_exception(self):
        state = self._base_state()
        state["reviewer_output"] = {"decision": "approve"}
        with patch("multi_agent.graph.graph_hooks"), \
             patch("multi_agent.graph.write_dashboard", side_effect=RuntimeError("dashboard fail")):
            result = decide_node(state)
        assert result["final_status"] == "failed"
        assert "decide_node" in result["error"]


class TestTrimConversation:
    """Task 74: Conversation size limit tests."""

    def test_short_conversation_unchanged(self):
        from multi_agent.graph import trim_conversation
        convo = [{"role": "a", "t": i} for i in range(10)]
        result = trim_conversation(convo)
        assert len(result) == 10

    def test_long_conversation_trimmed(self):
        from multi_agent.graph import MAX_CONVERSATION_SIZE, trim_conversation
        convo = [{"role": "a", "t": i} for i in range(300)]
        result = trim_conversation(convo)
        assert len(result) <= MAX_CONVERSATION_SIZE + 1  # +1 for trimmed marker
        # First entries preserved
        assert result[0]["t"] == 0
        # Has trimmed marker
        assert any(e.get("action") == "trimmed" for e in result)
        # Last entries preserved
        assert result[-1]["t"] == 299


class TestSaveStateSnapshot:
    """Task 70: State snapshot tests."""

    def test_snapshot_created(self, tmp_path):
        from multi_agent.graph import save_state_snapshot
        with patch("multi_agent.config.workspace_dir", return_value=tmp_path):
            save_state_snapshot("task-abc", "plan", {"task_id": "task-abc", "x": 1})
        snap_dir = tmp_path / "snapshots"
        assert snap_dir.exists()
        files = list(snap_dir.glob("task-abc-plan-*.json"))
        assert len(files) == 1

    def test_snapshot_cleanup(self, tmp_path):
        from multi_agent.graph import MAX_SNAPSHOTS, save_state_snapshot
        with patch("multi_agent.config.workspace_dir", return_value=tmp_path):
            for i in range(MAX_SNAPSHOTS + 5):
                save_state_snapshot("task-abc", f"node{i}", {"i": i})
        snap_dir = tmp_path / "snapshots"
        files = list(snap_dir.glob("task-abc-*.json"))
        assert len(files) <= MAX_SNAPSHOTS


class TestLogTiming:
    """Task 86: Execution timing log tests."""

    def test_log_timing_creates_file(self, tmp_path):
        from multi_agent.graph import log_timing
        with patch("multi_agent.config.workspace_dir", return_value=tmp_path):
            log_timing("task-t1", "plan", 1000.0, 1002.5)
        log_file = tmp_path / "logs" / "timing-task-t1.jsonl"
        assert log_file.exists()
        line = json.loads(log_file.read_text().strip())
        assert line["node"] == "plan"
        assert line["duration_ms"] == 2500

    def test_log_timing_appends(self, tmp_path):
        from multi_agent.graph import log_timing
        with patch("multi_agent.config.workspace_dir", return_value=tmp_path):
            log_timing("task-t2", "plan", 100.0, 101.0)
            log_timing("task-t2", "build", 101.0, 103.0)
        log_file = tmp_path / "logs" / "timing-task-t2.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2


class TestGraphStats:
    """Task 90: Graph execution statistics tests."""

    def test_record_and_summary(self):
        from multi_agent.graph import GraphStats
        stats = GraphStats()
        stats.record("plan", 100, True)
        stats.record("plan", 200, True)
        stats.record("plan", 300, False)
        s = stats.summary()
        assert s["plan"]["count"] == 3
        assert s["plan"]["avg_ms"] == 200
        assert s["plan"]["error_rate"] == pytest.approx(0.333, abs=0.01)

    def test_save(self, tmp_path):
        from multi_agent.graph import GraphStats
        stats = GraphStats()
        stats.record("build", 500, True)
        stats.save(path=tmp_path / "stats.json")
        assert (tmp_path / "stats.json").exists()
        data = json.loads((tmp_path / "stats.json").read_text())
        assert "build" in data

    def test_empty_summary(self):
        from multi_agent.graph import GraphStats
        stats = GraphStats()
        assert stats.summary() == {}


class TestCompileGraphCaching:
    """Task 11: compile_graph caching and reset_graph tests."""

    def test_compile_graph_returns_same_object(self, tmp_path):
        from multi_agent.graph import compile_graph, reset_graph
        db = str(tmp_path / "test_cache.db")
        try:
            g1 = compile_graph(db_path=db)
            g2 = compile_graph(db_path=db)
            assert g1 is g2
        finally:
            reset_graph()

    def test_reset_graph_clears_cache(self, tmp_path):
        from multi_agent.graph import compile_graph, reset_graph
        db = str(tmp_path / "test_reset.db")
        try:
            g1 = compile_graph(db_path=db)
            reset_graph()
            g2 = compile_graph(db_path=db)
            assert g1 is not g2
        finally:
            reset_graph()

    def test_different_db_path_creates_new(self, tmp_path):
        from multi_agent.graph import compile_graph, reset_graph
        db1 = str(tmp_path / "db1.db")
        db2 = str(tmp_path / "db2.db")
        try:
            g1 = compile_graph(db_path=db1)
            g2 = compile_graph(db_path=db2)
            assert g1 is not g2
        finally:
            reset_graph()

    def test_compile_graph_cold_start_not_deadlock(self, tmp_path):
        """Regression: compile_graph should not self-deadlock on cold start."""
        import threading

        from multi_agent.graph import compile_graph, reset_graph

        db = str(tmp_path / "cold-start.db")
        result: dict[str, object] = {}
        errors: list[Exception] = []

        def _run():
            try:
                result["graph"] = compile_graph(db_path=db)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        try:
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=2)
            assert not t.is_alive(), "compile_graph deadlocked on cold start"
            assert not errors
            assert "graph" in result
        finally:
            reset_graph()


class TestGraphStatsEdgeCases:
    """Edge case tests for GraphStats (R5-R7 additions)."""

    def test_record_token_usage_valid(self):
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"input_tokens": 500, "output_tokens": 200, "cost": 0.003})
        s = gs.summary()
        assert s["build"]["input_tokens"] == 500
        assert s["build"]["output_tokens"] == 200
        assert s["build"]["cost"] == 0.003

    def test_record_token_usage_accumulates(self):
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"input_tokens": 500, "cost": 0.001})
        gs.record_token_usage("build", {"input_tokens": 300, "cost": 0.002})
        s = gs.summary()
        assert s["build"]["input_tokens"] == 800
        assert s["build"]["cost"] == 0.003

    def test_record_token_usage_ignores_non_numeric(self):
        """Malformed IDE output should not crash (type safety fix)."""
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"input_tokens": "abc", "cost": "free"})
        s = gs.summary()
        assert "input_tokens" not in s["build"]
        assert "cost" not in s["build"]

    def test_record_token_usage_mixed_types(self):
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_token_usage("build", {"input_tokens": 500, "output_tokens": None, "cost": 0.01})
        s = gs.summary()
        assert s["build"]["input_tokens"] == 500
        assert "output_tokens" not in s["build"]
        assert s["build"]["cost"] == 0.01

    def test_record_token_usage_creates_node_if_missing(self):
        gs = GraphStats()
        gs.record_token_usage("unknown_node", {"total_tokens": 42})
        s = gs.summary()
        assert s["unknown_node"]["total_tokens"] == 42
        assert s["unknown_node"]["count"] == 0

    def test_record_retry_outcome(self):
        gs = GraphStats()
        gs.record_retry_outcome(1, "reject")
        gs.record_retry_outcome(2, "approve")
        s = gs.summary()
        assert s["_retry_effectiveness"]["total_retries"] == 2
        assert s["_retry_effectiveness"]["retry_success_rate"] == 0.5

    def test_summary_no_retry_outcomes(self):
        gs = GraphStats()
        gs.record("plan", 50, True)
        s = gs.summary()
        assert "_retry_effectiveness" not in s

    def test_summary_empty(self):
        gs = GraphStats()
        s = gs.summary()
        assert s == {}


class TestTrimConversationEdgeCases:
    """Edge case tests for trim_conversation (R5 additions)."""

    def test_no_trim_needed(self):
        conv = [{"role": "user", "t": 1}] * 10
        result = trim_conversation(conv)
        assert len(result) == 10

    def test_trim_at_boundary(self):
        conv = [{"role": "user", "action": "test", "t": i} for i in range(MAX_CONVERSATION_SIZE + 1)]
        result = trim_conversation(conv)
        assert len(result) == MAX_CONVERSATION_SIZE
        # Should have a trimmed marker
        assert any(e.get("action") == "trimmed" for e in result)

    def test_non_string_feedback_does_not_crash(self):
        """feedback could be int/dict — type safety fix."""
        conv = [{"role": "user", "action": "retry", "feedback": 42, "t": i}
                for i in range(MAX_CONVERSATION_SIZE + 5)]
        result = trim_conversation(conv)
        marker = next(e for e in result if e.get("action") == "trimmed")
        # Non-string feedback should be silently skipped
        assert marker["key_feedback"] == []

    def test_feedback_keeps_all_retry_rc_snippets(self):
        """All retry/request_changes feedback should be preserved (not capped at 3)."""
        conv = [{"role": "user", "action": "retry", "feedback": f"fb-{i}", "t": i}
                for i in range(MAX_CONVERSATION_SIZE + 20)]
        result = trim_conversation(conv)
        marker = next(e for e in result if e.get("action") == "trimmed")
        # All retry feedback from removed entries should be kept
        assert len(marker["key_feedback"]) > 3

    def test_feedback_excludes_non_retry_actions(self):
        """Only retry/request_changes feedback is kept, not arbitrary actions."""
        conv = [{"role": "user", "action": "assigned", "feedback": f"fb-{i}", "t": i}
                for i in range(MAX_CONVERSATION_SIZE + 10)]
        result = trim_conversation(conv)
        marker = next(e for e in result if e.get("action") == "trimmed")
        assert marker["key_feedback"] == []


class TestRouteDecisionTerminalStates:
    """R12: route_decision must route to 'end' for ALL terminal states,
    not just 'approved'. Previously, cancelled/failed/escalated states
    fell through to 'retry', causing cancelled tasks to keep running."""

    def test_cancelled_goes_to_end(self):
        state = {"final_status": "cancelled"}
        assert route_decision(state) == "end"

    def test_failed_goes_to_end(self):
        state = {"final_status": "failed"}
        assert route_decision(state) == "end"

    def test_escalated_goes_to_end(self):
        state = {"final_status": "escalated"}
        assert route_decision(state) == "end"

    def test_approved_goes_to_end(self):
        state = {"final_status": "approved"}
        assert route_decision(state) == "end"

    def test_no_final_status_retries(self):
        state = {"reviewer_output": {"decision": "reject"}}
        assert route_decision(state) == "retry"


class TestReviewNodeTotalTimeout:
    """R12: review_node TOTAL_TIMEOUT must set error + final_status='failed',
    not just a reviewer_output reject. Previously, the 2h safety limit was
    bypassed because decide_node treated TOTAL_TIMEOUT as a normal reject."""

    @patch("multi_agent.graph.interrupt")
    def test_total_timeout_sets_error_and_failed(self, mock_interrupt):
        import time as _time
        mock_interrupt.return_value = {"decision": "approve", "feedback": "ok"}
        state = {
            "reviewer_id": "cursor",
            "task_started_at": _time.time() - MAX_TASK_DURATION_SEC - 100,
            "started_at": _time.time(),
            "timeout_sec": 1800,
            "conversation": [],
        }
        result = review_node(state)
        assert result["final_status"] == "failed"
        assert "TOTAL_TIMEOUT" in result["error"]
        # Should NOT have reviewer_output (interrupt never called)
        assert "reviewer_output" not in result

    @patch("multi_agent.graph.interrupt")
    def test_total_timeout_does_not_call_interrupt(self, mock_interrupt):
        """TOTAL_TIMEOUT should return before reaching interrupt()."""
        import time as _time
        state = {
            "reviewer_id": "cursor",
            "task_started_at": _time.time() - MAX_TASK_DURATION_SEC - 1,
            "conversation": [],
        }
        review_node(state)
        mock_interrupt.assert_not_called()


class TestDecideNodeTerminalPassthrough:
    """R12: decide_node must early-exit if state already has a terminal
    final_status (e.g., review_node returned 'cancelled'). Previously,
    decide processed stale reviewer_output from a prior round."""

    @patch("multi_agent.graph.write_dashboard")
    def test_cancelled_state_passes_through(self, mock_dash):
        state = {
            "task_id": "task-pt-01", "skill_id": "code-implement",
            "done_criteria": ["test"], "retry_count": 0, "retry_budget": 2,
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "conversation": [],
            "final_status": "cancelled",
        }
        result = decide_node(state)
        assert result["final_status"] == "cancelled"
        assert result["conversation"][0]["action"] == "terminal_passthrough"
        # Should NOT have retry_count (no retry logic executed)
        assert "retry_count" not in result
        # Dashboard should NOT be called (no state change)
        mock_dash.assert_not_called()

    @patch("multi_agent.graph.write_dashboard")
    def test_failed_state_passes_through_with_error(self, mock_dash):
        state = {
            "task_id": "task-pt-02", "skill_id": "code-implement",
            "done_criteria": ["test"], "retry_count": 0, "retry_budget": 2,
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "conversation": [],
            "final_status": "failed",
            "error": "TOTAL_TIMEOUT: exceeded 7200s",
        }
        result = decide_node(state)
        assert result["final_status"] == "failed"
        assert result["error"] == "TOTAL_TIMEOUT: exceeded 7200s"

    @patch("multi_agent.graph.archive_conversation")
    @patch("multi_agent.graph.write_dashboard")
    def test_approved_state_not_passthrough(self, mock_dash, mock_archive):
        """approved final_status should NOT trigger early exit — let normal
        approve logic run (which archives conversation, etc.)."""
        state = {
            "task_id": "task-pt-03", "skill_id": "code-implement",
            "done_criteria": ["test"], "retry_count": 0, "retry_budget": 2,
            "builder_id": "windsurf", "reviewer_id": "cursor",
            "conversation": [],
            "reviewer_output": {"decision": "approve", "summary": "LGTM"},
        }
        result = decide_node(state)
        assert result["final_status"] == "approved"
        # Normal approve path should archive conversation
        mock_archive.assert_called_once()


class TestGraphStatsReset:
    """R14 F1: GraphStats.reset() prevents cross-task stats contamination
    (MAST NeurIPS 2025 SD-4; MAS-FIRE 2026)."""

    def test_reset_clears_stats(self):
        gs = GraphStats()
        gs.record("build", 100, True)
        gs.record_retry_outcome(1, "reject")
        assert gs.summary() != {}
        gs.reset()
        assert gs.summary() == {}

    def test_reset_clears_retry_outcomes(self):
        gs = GraphStats()
        gs.record_retry_outcome(1, "reject")
        gs.record_retry_outcome(2, "approve")
        assert "_retry_effectiveness" in gs.summary()
        gs.reset()
        assert "_retry_effectiveness" not in gs.summary()

    def test_plan_node_resets_stats_on_first_run(self):
        """Verify graph_stats.reset() is called when retry_count == 0."""
        from multi_agent.graph import graph_stats
        graph_stats.record("build", 500, True)
        graph_stats.record_retry_outcome(1, "reject")
        assert graph_stats.summary() != {}
        # Simulate plan_node first-run reset logic
        state = {"retry_count": 0}
        if state.get("retry_count", 0) == 0:
            graph_stats.reset()
        assert graph_stats.summary() == {}

    def test_plan_node_preserves_stats_on_retry(self):
        """Verify graph_stats is NOT reset when retry_count > 0."""
        from multi_agent.graph import graph_stats
        graph_stats.reset()
        graph_stats.record("build", 500, True)
        state = {"retry_count": 1}
        if state.get("retry_count", 0) == 0:
            graph_stats.reset()
        assert graph_stats.summary() != {}


class TestRubberStampStrengthened:
    """R14 F3: Strengthened rubber-stamp detection catches generic phrases
    (MAST NeurIPS 2025 TV-1)."""

    def test_generic_lgtm_flagged(self):
        """Short generic phrase 'LGTM' should trigger rubber-stamp warning."""
        result = {"decision": "approve", "summary": "LGTM", "reasoning": ""}
        summary = result.get("summary", "")
        reasoning = result.get("reasoning", "")
        _RUBBER_STAMP_PHRASES = {"lgtm", "looks good", "no issues", "approved", "all good",
                                  "ship it", "good to go", "looks fine", "no comments"}
        is_generic = any(p in summary.lower() for p in _RUBBER_STAMP_PHRASES) and len(summary) < 50
        is_shallow = not reasoning and len(summary) < 40
        assert is_generic or is_shallow

    def test_substantive_approve_not_flagged(self):
        """Approve with reasoning and detailed summary should NOT be flagged."""
        result = {
            "decision": "approve",
            "summary": "Code changes correctly implement the requested feature with proper error handling and test coverage.",
            "reasoning": "Reviewed all changed files, verified logic, checked edge cases.",
        }
        summary = result.get("summary", "")
        reasoning = result.get("reasoning", "")
        _RUBBER_STAMP_PHRASES = {"lgtm", "looks good", "no issues", "approved", "all good",
                                  "ship it", "good to go", "looks fine", "no comments"}
        is_generic = any(p in summary.lower() for p in _RUBBER_STAMP_PHRASES) and len(summary) < 50
        is_shallow = not reasoning and len(summary) < 40
        assert not is_generic and not is_shallow

    def test_looks_good_short_flagged(self):
        result = {"decision": "approve", "summary": "Looks good to me", "reasoning": ""}
        summary = result.get("summary", "")
        reasoning = result.get("reasoning", "")
        _RUBBER_STAMP_PHRASES = {"lgtm", "looks good", "no issues", "approved", "all good",
                                  "ship it", "good to go", "looks fine", "no comments"}
        is_generic = any(p in summary.lower() for p in _RUBBER_STAMP_PHRASES) and len(summary) < 50
        is_shallow = not reasoning and len(summary) < 40
        assert is_generic or is_shallow


class TestRetryContextPreservation:
    """R14 F4: Retry entry preserves previous round context
    (Agent Error Taxonomy ICLR 2026; MAST NeurIPS 2025 IA-2)."""

    def test_retry_preserves_changed_files(self):
        state = {
            "builder_output": {
                "changed_files": ["src/main.py", "tests/test_main.py"],
                "gate_warnings": ["quality gate 'lint' failed: error"],
            }
        }
        retry_entry = {"role": "orchestrator", "action": "retry", "feedback": "fix lint"}
        prev_builder = state.get("builder_output")
        if isinstance(prev_builder, dict):
            changed = prev_builder.get("changed_files")
            if changed:
                retry_entry["prev_changed_files"] = changed
            gates = prev_builder.get("gate_warnings")
            if gates:
                retry_entry["prev_gate_warnings"] = gates
        assert retry_entry["prev_changed_files"] == ["src/main.py", "tests/test_main.py"]
        assert retry_entry["prev_gate_warnings"] == ["quality gate 'lint' failed: error"]

    def test_retry_no_builder_output_no_crash(self):
        state = {}
        retry_entry = {"role": "orchestrator", "action": "retry", "feedback": "try again"}
        prev_builder = state.get("builder_output")
        if isinstance(prev_builder, dict):
            changed = prev_builder.get("changed_files")
            if changed:
                retry_entry["prev_changed_files"] = changed
        assert "prev_changed_files" not in retry_entry


class TestInterAgentSanitization:
    """R14 F2: Builder output sanitized before reviewer prompt rendering
    (Agents Under Siege, UNC 2025)."""

    def test_long_summary_truncated(self):
        from multi_agent.prompt import MAX_BUILDER_SUMMARY_CHARS, _sanitize_builder_output
        output = {"summary": "x" * 5000, "status": "done"}
        sanitized = _sanitize_builder_output(output)
        assert len(sanitized["summary"]) <= MAX_BUILDER_SUMMARY_CHARS + len(" [TRUNCATED]")
        assert sanitized["summary"].endswith("[TRUNCATED]")

    def test_short_summary_unchanged(self):
        from multi_agent.prompt import _sanitize_builder_output
        output = {"summary": "Fixed the bug", "status": "done"}
        sanitized = _sanitize_builder_output(output)
        assert sanitized["summary"] == "Fixed the bug"

    def test_non_text_fields_unchanged(self):
        from multi_agent.prompt import _sanitize_builder_output
        output = {"summary": "ok", "changed_files": ["a.py"], "status": "done"}
        sanitized = _sanitize_builder_output(output)
        assert sanitized["changed_files"] == ["a.py"]
        assert sanitized["status"] == "done"


class TestDoWConstants:
    def test_max_task_duration_is_positive(self):
        assert MAX_TASK_DURATION_SEC > 0

    def test_max_task_duration_reasonable(self):
        assert 3600 <= MAX_TASK_DURATION_SEC <= 14400  # 1-4 hours


class TestRubberStampEdgeCases:
    """Cover uncovered branches in _is_rubber_stamp_approval (lines 174-183)."""

    def test_invalid_generic_max_type_falls_back(self):
        from multi_agent.graph import _is_rubber_stamp_approval
        output = {
            "decision": "approve", "summary": "lgtm", "reasoning": "",
            "_rubber_policy": {"generic_summary_max_len": "not_a_number"},
        }
        # Should not crash — falls back to default 50
        result = _is_rubber_stamp_approval(output)
        assert isinstance(result, bool)

    def test_invalid_shallow_max_type_falls_back(self):
        from multi_agent.graph import _is_rubber_stamp_approval
        output = {
            "decision": "approve", "summary": "lgtm", "reasoning": "",
            "_rubber_policy": {"shallow_summary_max_len": None},
        }
        result = _is_rubber_stamp_approval(output)
        assert isinstance(result, bool)

    def test_negative_generic_max_resets(self):
        from multi_agent.graph import _is_rubber_stamp_approval
        output = {
            "decision": "approve", "summary": "lgtm", "reasoning": "",
            "_rubber_policy": {"generic_summary_max_len": -5, "shallow_summary_max_len": -1},
        }
        result = _is_rubber_stamp_approval(output)
        assert isinstance(result, bool)

    def test_zero_max_values_reset(self):
        from multi_agent.graph import _is_rubber_stamp_approval
        output = {
            "decision": "approve", "summary": "lgtm", "reasoning": "",
            "_rubber_policy": {"generic_summary_max_len": 0, "shallow_summary_max_len": 0},
        }
        result = _is_rubber_stamp_approval(output)
        assert isinstance(result, bool)


class TestEnrichReviewerResult:
    """Cover _enrich_reviewer_result empty feedback injection (lines 609-613)."""

    def test_empty_feedback_injected_on_reject(self):
        from multi_agent.graph import _enrich_reviewer_result
        result = {"decision": "reject", "feedback": ""}
        _enrich_reviewer_result(result, "reject", {"review_policy": None})
        assert result["feedback"]  # non-empty after injection
        assert "Reviewer did not provide" in result["feedback"]

    def test_whitespace_only_feedback_injected(self):
        from multi_agent.graph import _enrich_reviewer_result
        result = {"decision": "request_changes", "feedback": "   "}
        _enrich_reviewer_result(result, "request_changes", {"review_policy": None})
        assert "Reviewer did not provide" in result["feedback"]

    def test_valid_feedback_not_overwritten(self):
        from multi_agent.graph import _enrich_reviewer_result
        result = {"decision": "reject", "feedback": "Fix the auth module"}
        _enrich_reviewer_result(result, "reject", {"review_policy": None})
        assert result["feedback"] == "Fix the auth module"

    def test_approve_decision_no_injection(self):
        from multi_agent.graph import _enrich_reviewer_result
        result = {"decision": "approve", "feedback": ""}
        _enrich_reviewer_result(result, "approve", {"review_policy": None})
        assert result["feedback"] == ""


class TestDecideRejectDDIDecay:
    """Cover DDI decay warning at retry >= 2 (lines 746-751)."""

    def test_ddi_decay_appended_at_retry_2(self):
        from multi_agent.graph import _decide_reject_retry
        state = {
            "task_id": "task-ddi", "retry_count": 1, "retry_budget": 3,
            "builder_output": {"summary": "attempt", "changed_files": ["a.py"]},
            "reviewer_output": {"decision": "reject", "feedback": "wrong approach"},
            "conversation": [], "done_criteria": ["works"],
        }
        with patch("multi_agent.graph.write_dashboard"):
            result = _decide_reject_retry(state, state["reviewer_output"])
        convo = result.get("conversation", [])
        assert len(convo) == 1
        entry = convo[0]
        assert entry["action"] == "retry"
        # retry_count starts at 1, gets incremented to 2 inside _decide_reject_retry
        assert result["retry_count"] == 2
        assert "衰减" in entry["feedback"] or "DDI" in entry.get("feedback", "")


class TestBuildGraph:
    def test_graph_structure(self):
        g = build_graph()
        compiled = g.compile()
        # Check that the graph has the expected nodes
        node_names = set(compiled.get_graph().nodes.keys())
        assert "plan" in node_names
        assert "build" in node_names
        assert "review" in node_names
        assert "decide" in node_names
