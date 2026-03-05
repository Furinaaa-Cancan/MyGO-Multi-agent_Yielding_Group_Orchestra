"""Tests for meta-graph orchestration module."""

from multi_agent.meta_graph import (
    aggregate_results,
    build_sub_task_state,
    format_prior_context,
    generate_aggregate_report,
    generate_sub_task_id,
)
from multi_agent.schema import SubTask


class TestGenerateSubTaskId:
    def test_deterministic(self):
        id1 = generate_sub_task_id("task-parent-123", "auth-login")
        id2 = generate_sub_task_id("task-parent-123", "auth-login")
        assert id1 == id2

    def test_different_for_different_sub(self):
        id1 = generate_sub_task_id("task-parent-123", "auth-login")
        id2 = generate_sub_task_id("task-parent-123", "auth-register")
        assert id1 != id2

    def test_format(self):
        tid = generate_sub_task_id("task-auth-impl", "login")
        assert tid.startswith("task-")
        assert "auth" in tid
        assert "login" in tid

    def test_readable_id(self):
        tid = generate_sub_task_id("task-auth-impl", "login")
        assert tid == "task-auth-impl-login"

    def test_special_chars_cleaned(self):
        tid = generate_sub_task_id("task-parent", "Hello World!")
        assert tid.startswith("task-")
        assert " " not in tid
        assert "!" not in tid

    def test_long_input_truncated(self):
        import re
        _ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
        tid = generate_sub_task_id("task-very-long-parent-name-here", "a" * 50)
        assert _ID_RE.match(tid)
        assert len(tid) <= 64

    def test_fallback_to_hash(self):
        # sub_id with only special chars → cleaned to empty → fallback
        tid = generate_sub_task_id("task-x", "!!!")
        assert tid.startswith("task-")
        assert len(tid) >= 5


class TestBuildSubTaskState:
    def test_basic(self):
        st = SubTask(id="auth-login", description="Implement login")
        state = build_sub_task_state(st, "parent-abc")
        assert state["task_id"].startswith("task-")
        assert "Implement login" in state["requirement"]
        assert state["skill_id"] == "code-implement"
        assert state["retry_count"] == 0

    def test_with_prior_results(self):
        st = SubTask(id="auth-middleware", description="Implement middleware")
        prior = [
            {"sub_id": "auth-login", "summary": "Login done", "changed_files": ["/src/login.py"]},
        ]
        state = build_sub_task_state(st, "parent-abc", prior_results=prior)
        assert "auth-login" in state["requirement"]
        assert "Login done" in state["requirement"]
        assert "/src/login.py" in state["requirement"]

    def test_with_explicit_agents(self):
        st = SubTask(id="step-1", description="Do something")
        state = build_sub_task_state(
            st, "parent-abc", builder="windsurf", reviewer="cursor",
        )
        assert state["builder_explicit"] == "windsurf"
        assert state["reviewer_explicit"] == "cursor"

    def test_custom_timeout_and_budget(self):
        st = SubTask(id="step-1", description="Do something")
        state = build_sub_task_state(
            st, "parent-abc", timeout=900, retry_budget=3,
        )
        assert state["timeout_sec"] == 900
        assert state["retry_budget"] == 3

    def test_workflow_mode_and_policy_propagation(self):
        st = SubTask(id="step-1", description="Do something")
        policy = {"reviewer": {"require_evidence_on_approve": True, "min_evidence_items": 2}}
        state = build_sub_task_state(
            st, "parent-abc", workflow_mode="normal", review_policy=policy,
        )
        assert state["workflow_mode"] == "normal"
        assert state["review_policy"] == policy


class TestAggregateResults:
    def test_all_approved(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done A",
             "changed_files": ["/a.py"], "retry_count": 0},
            {"sub_id": "b", "status": "approved", "summary": "Done B",
             "changed_files": ["/b.py"], "retry_count": 1},
        ]
        agg = aggregate_results("parent-123", results)
        assert agg["final_status"] == "approved"
        assert agg["total_sub_tasks"] == 2
        assert agg["completed"] == 2
        assert agg["failed"] == []
        assert agg["total_retries"] == 1
        assert set(agg["all_changed_files"]) == {"/a.py", "/b.py"}

    def test_with_failure(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done",
             "changed_files": [], "retry_count": 0},
            {"sub_id": "b", "status": "failed", "summary": "Crashed",
             "changed_files": [], "retry_count": 2},
        ]
        agg = aggregate_results("parent-123", results)
        assert agg["final_status"] == "failed"
        assert agg["completed"] == 1
        assert agg["failed"] == ["b"]

    def test_empty_results(self):
        agg = aggregate_results("parent-123", [])
        assert agg["total_sub_tasks"] == 0
        assert agg["final_status"] == "approved"

    def test_skipped_counts_as_failed(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done",
             "changed_files": [], "retry_count": 0},
            {"sub_id": "b", "status": "skipped", "summary": "Dep failed",
             "changed_files": [], "retry_count": 0},
        ]
        agg = aggregate_results("parent-123", results)
        assert agg["final_status"] == "failed"
        assert agg["failed"] == ["b"]
        assert agg["completed"] == 1

    def test_dedup_changed_files(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "",
             "changed_files": ["/shared.py", "/a.py"], "retry_count": 0},
            {"sub_id": "b", "status": "approved", "summary": "",
             "changed_files": ["/shared.py", "/b.py"], "retry_count": 0},
        ]
        agg = aggregate_results("parent-123", results)
        assert len(agg["all_changed_files"]) == 3  # deduped


class TestFormatPriorContext:
    """Task 18: Verify prior context formatting."""

    def test_empty_results(self):
        assert format_prior_context([]) == ""

    def test_single_result(self):
        prior = [{"sub_id": "a", "summary": "Done A", "changed_files": ["/a.py"]}]
        ctx = format_prior_context(prior)
        assert "a" in ctx
        assert "Done A" in ctx
        assert "/a.py" in ctx

    def test_includes_reviewer_feedback(self):
        prior = [{"sub_id": "a", "summary": "Done", "reviewer_feedback": "Add tests"}]
        ctx = format_prior_context(prior)
        assert "Add tests" in ctx
        assert "Reviewer" in ctx

    def test_max_3_items(self):
        prior = [
            {"sub_id": f"t{i}", "summary": f"Done {i}"} for i in range(5)
        ]
        ctx = format_prior_context(prior)
        # Only t2, t3, t4 should appear (last 3)
        assert "t2" in ctx
        assert "t3" in ctx
        assert "t4" in ctx
        assert "t0" not in ctx
        assert "t1" not in ctx

    def test_custom_max_items(self):
        prior = [{"sub_id": f"t{i}", "summary": f"D{i}"} for i in range(5)]
        ctx = format_prior_context(prior, max_items=2)
        assert "t3" in ctx
        assert "t4" in ctx
        assert "t2" not in ctx

    def test_no_reviewer_feedback_omits_line(self):
        prior = [{"sub_id": "a", "summary": "Done"}]
        ctx = format_prior_context(prior)
        assert "Reviewer" not in ctx


class TestBuildSubTaskStateEnhanced:
    """Task 18 + Task 5: Verify acceptance_criteria merge and parent_task_id."""

    def test_acceptance_criteria_merged(self):
        st = SubTask(
            id="auth", description="Auth",
            done_criteria=["login works"],
            acceptance_criteria=["tests pass", "no regressions"],
        )
        state = build_sub_task_state(st, "parent-abc")
        assert "login works" in state["done_criteria"]
        assert "tests pass" in state["done_criteria"]
        assert "no regressions" in state["done_criteria"]

    def test_parent_task_id_set(self):
        st = SubTask(id="step-1", description="Do something")
        state = build_sub_task_state(st, "parent-abc")
        assert state["parent_task_id"] == "parent-abc"

    def test_prior_results_with_feedback(self):
        st = SubTask(id="step-2", description="Next step")
        prior = [
            {"sub_id": "step-1", "summary": "Done", "reviewer_feedback": "Needs cleanup"},
        ]
        state = build_sub_task_state(st, "parent-abc", prior_results=prior)
        assert "Needs cleanup" in state["requirement"]
        assert "Reviewer" in state["requirement"]


class TestDurationStats:
    """Task 30: Verify duration stats in aggregate_results."""

    def test_duration_stats_present(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done",
             "changed_files": [], "retry_count": 0, "duration_sec": 120},
            {"sub_id": "b", "status": "approved", "summary": "Done",
             "changed_files": [], "retry_count": 0, "duration_sec": 300},
        ]
        agg = aggregate_results("parent", results)
        assert agg["total_duration_sec"] == 420
        assert agg["avg_duration_sec"] == 210.0
        assert agg["slowest_sub_task"] == "b"
        assert agg["slowest_duration_sec"] == 300

    def test_duration_zero_for_skipped(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "",
             "changed_files": [], "retry_count": 0, "duration_sec": 60},
            {"sub_id": "b", "status": "skipped", "summary": "",
             "changed_files": [], "retry_count": 0, "duration_sec": 0},
        ]
        agg = aggregate_results("parent", results)
        assert agg["total_duration_sec"] == 60
        assert agg["slowest_sub_task"] == "a"

    def test_empty_results_duration(self):
        agg = aggregate_results("parent", [])
        assert agg["total_duration_sec"] == 0
        assert agg["avg_duration_sec"] == 0


class TestGenerateAggregateReport:
    """Task 26: Verify Markdown report generation."""

    def test_report_contains_all_sections(self):
        results = [
            {"sub_id": "auth-login", "status": "approved", "summary": "实现登录",
             "changed_files": ["/src/login.py"], "retry_count": 0, "duration_sec": 120},
            {"sub_id": "auth-reg", "status": "failed", "summary": "注册失败",
             "changed_files": [], "retry_count": 2, "duration_sec": 300},
        ]
        agg = aggregate_results("parent-123", results)
        report = generate_aggregate_report(agg)
        assert "# 任务分解执行报告" in report
        assert "## 概要" in report
        assert "## 详情" in report
        assert "auth-login" in report
        assert "auth-reg" in report
        assert "✅ 通过" in report
        assert "❌ 失败" in report
        assert "## 修改文件" in report
        assert "/src/login.py" in report

    def test_report_with_duration(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done",
             "changed_files": [], "retry_count": 0, "duration_sec": 754},
        ]
        agg = aggregate_results("parent", results)
        report = generate_aggregate_report(agg)
        assert "总耗时" in report
        assert "最慢子任务" in report

    def test_report_empty_results(self):
        agg = aggregate_results("parent", [])
        report = generate_aggregate_report(agg)
        assert "# 任务分解执行报告" in report
        assert "总子任务: 0" in report

    def test_report_skipped_status(self):
        results = [
            {"sub_id": "a", "status": "skipped", "summary": "Dep failed",
             "changed_files": [], "retry_count": 0, "duration_sec": 0},
        ]
        agg = aggregate_results("parent", results)
        report = generate_aggregate_report(agg)
        assert "⏭️ 跳过" in report

    def test_report_no_files_section_when_empty(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done",
             "changed_files": [], "retry_count": 0, "duration_sec": 0},
        ]
        agg = aggregate_results("parent", results)
        report = generate_aggregate_report(agg)
        assert "## 修改文件" not in report


class TestBuildSubTaskStateOrchestratorId:
    """Regression: build_sub_task_state must include orchestrator_id."""

    def test_orchestrator_id_present(self):
        from unittest.mock import patch
        st = SubTask(id="auth", description="impl auth", done_criteria=["works"])
        with patch("multi_agent.router.get_defaults", return_value={"orchestrator": "claude"}):
            state = build_sub_task_state(st, parent_task_id="task-parent")
        assert "orchestrator_id" in state
        assert state["orchestrator_id"] == "claude"

    def test_orchestrator_id_defaults_to_codex(self):
        from unittest.mock import patch
        st = SubTask(id="db", description="impl db", done_criteria=["works"])
        with patch("multi_agent.router.get_defaults", return_value={}):
            state = build_sub_task_state(st, parent_task_id="task-parent")
        assert state["orchestrator_id"] == "codex"


class TestLoadCheckpointEdgeCases:
    """Cover load_checkpoint exception handling (lines 54->59)."""

    def test_corrupt_checkpoint_returns_none(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.meta_graph import load_checkpoint
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        ckpt_dir = tmp_path / "checkpoints"
        ckpt_dir.mkdir(parents=True)
        (ckpt_dir / "decompose-task-bad.json").write_text("not json{{{")
        result = load_checkpoint("task-bad")
        assert result is None

    def test_missing_keys_returns_none(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.meta_graph import load_checkpoint
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        ckpt_dir = tmp_path / "checkpoints"
        ckpt_dir.mkdir(parents=True)
        (ckpt_dir / "decompose-task-nokeys.json").write_text('{"foo": "bar"}')
        result = load_checkpoint("task-nokeys")
        assert result is None


class TestClearCheckpointEdgeCases:
    """Cover clear_checkpoint when file doesn't exist (line 66->exit)."""

    def test_clear_nonexistent_no_error(self, tmp_path, monkeypatch):
        from multi_agent import config
        from multi_agent.meta_graph import clear_checkpoint
        monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
        clear_checkpoint("task-nonexistent")  # should not raise


class TestGenerateSubTaskIdFallback:
    """Cover hash-based fallback ID (lines 93-94)."""

    def test_invalid_chars_trigger_hash_fallback(self):
        # Sub ID with characters that create invalid task ID after cleaning
        tid = generate_sub_task_id("task-x", "---")
        assert tid.startswith("task-")
        # Should still produce a valid ID
        assert len(tid) > 5


class TestGenerateAggregateReportEdgeCases:
    """Cover slowest sub-task and estimated time report (lines 275-286)."""

    def test_report_with_slowest_and_estimation(self):
        results = [
            {"sub_id": "a", "status": "approved", "summary": "Done",
             "changed_files": ["x.py"], "retry_count": 0, "duration_sec": 120},
            {"sub_id": "b", "status": "approved", "summary": "Done too",
             "changed_files": ["y.py"], "retry_count": 1, "duration_sec": 300},
        ]
        agg = aggregate_results("parent", results)
        # Inject estimated time to cover lines 282-286
        agg["estimated_total_minutes"] = 5
        agg["actual_total_minutes"] = 7
        report = generate_aggregate_report(agg)
        assert "预估总时间" in report
        assert "实际总时间" in report
        assert "准确率" in report
        # Should also have slowest sub-task
        assert "最慢子任务" in report or "b" in report
