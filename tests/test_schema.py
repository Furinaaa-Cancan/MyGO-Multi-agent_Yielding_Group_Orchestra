"""Tests for Pydantic schema models."""

import pytest

from multi_agent.schema import (
    AgentProfile,
    BackoffStrategy,
    BuilderOutput,
    CheckKind,
    DecomposeResult,
    Priority,
    ReviewDecision,
    ReviewerOutput,
    SkillContract,
    SubTask,
    Task,
    TaskError,
    TaskState,
)


class TestTask:
    def test_valid_task(self):
        t = Task(
            task_id="task-abc-123",
            trace_id="a" * 16,
            skill_id="code-implement",
            done_criteria=["implement endpoint"],
            expected_checks=[CheckKind.LINT, CheckKind.UNIT_TEST],
        )
        assert t.state == TaskState.DRAFT
        assert t.priority == Priority.NORMAL
        assert t.retry_budget == 2

    def test_invalid_task_id(self):
        with pytest.raises(ValueError, match="task_id"):
            Task(task_id="INVALID", trace_id="a" * 16, skill_id="code-implement")

    def test_invalid_trace_id(self):
        with pytest.raises(ValueError, match="trace_id"):
            Task(task_id="task-abc", trace_id="ZZZ", skill_id="code-implement")

    def test_task_with_error(self):
        t = Task(
            task_id="task-err",
            trace_id="b" * 16,
            skill_id="code-implement",
            state=TaskState.FAILED,
            error=TaskError(code="TIMEOUT", message="ran too long"),
        )
        assert t.error.code == "TIMEOUT"


class TestSkillContract:
    def test_from_yaml(self):
        data = {
            "id": "code-implement",
            "version": "1.0.0",
            "description": "Apply scoped code changes",
            "quality_gates": ["lint", "unit_test"],
            "timeouts": {"run_sec": 1800, "verify_sec": 600},
            "retry": {"max_attempts": 2, "backoff": "linear"},
            "compatibility": {
                "min_orchestrator_version": "0.1.0",
                "supported_agents": ["codex", "windsurf"],
            },
        }
        c = SkillContract.from_yaml(data)
        assert c.id == "code-implement"
        assert c.supported_agents == ["codex", "windsurf"]
        assert c.timeouts.run_sec == 1800
        assert c.retry.backoff == BackoffStrategy.LINEAR

    def test_from_yaml_no_agents(self):
        data = {"id": "test-skill", "version": "1.0.0"}
        c = SkillContract.from_yaml(data)
        assert c.supported_agents == []


class TestAgentOutput:
    def test_builder_output(self):
        o = BuilderOutput(
            status="completed",
            summary="implemented endpoint",
            changed_files=["/src/main.py"],
            check_results={"lint": "pass", "unit_test": "pass"},
        )
        assert o.status == "completed"

    def test_reviewer_approve(self):
        o = ReviewerOutput(decision=ReviewDecision.APPROVE, summary="LGTM")
        assert o.decision == ReviewDecision.APPROVE

    def test_reviewer_reject(self):
        o = ReviewerOutput(
            decision=ReviewDecision.REJECT,
            issues=["missing validation"],
            feedback="Add email format check",
        )
        assert len(o.issues) == 1


class TestAgentProfile:
    def test_profile(self):
        p = AgentProfile(
            id="windsurf",
            capabilities=["planning", "implementation"],
            reliability=0.88,
            queue_health=0.91,
            cost=0.50,
        )
        assert p.id == "windsurf"
        assert "implementation" in p.capabilities

    def test_driver_defaults_to_file(self):
        p = AgentProfile(id="windsurf")
        assert p.driver == "file"
        assert p.command == ""

    def test_cli_driver_with_command(self):
        p = AgentProfile(
            id="claude",
            driver="cli",
            command="claude -p '{task_file}'",
        )
        assert p.driver == "cli"
        assert "{task_file}" in p.command


class TestSubTask:
    """Task 5: Verify SubTask new fields."""

    def test_defaults(self):
        st = SubTask(id="auth-login", description="implement login")
        assert st.priority == Priority.NORMAL
        assert st.estimated_minutes == 30
        assert st.acceptance_criteria == []
        assert st.parent_task_id is None

    def test_custom_values(self):
        st = SubTask(
            id="auth-login",
            description="implement login",
            priority=Priority.HIGH,
            estimated_minutes=60,
            acceptance_criteria=["tests pass", "no regressions"],
            parent_task_id="task-parent-01",
        )
        assert st.priority == Priority.HIGH
        assert st.estimated_minutes == 60
        assert len(st.acceptance_criteria) == 2
        assert st.parent_task_id == "task-parent-01"

    def test_backward_compatible_no_new_fields(self):
        """Old JSON without new fields should parse fine."""
        data = {"id": "auth-login", "description": "login", "deps": []}
        st = SubTask(**data)
        assert st.priority == Priority.NORMAL
        assert st.estimated_minutes == 30


class TestDecomposeResult:
    """Task 5 & 27: Verify DecomposeResult new fields."""

    def test_defaults(self):
        dr = DecomposeResult(sub_tasks=[SubTask(id="a", description="do A")])
        assert dr.total_estimated_minutes == 0
        assert dr.version == "1.0"
        assert dr.metadata == {}
        assert dr.created_at  # non-empty string

    def test_custom_values(self):
        dr = DecomposeResult(
            sub_tasks=[SubTask(id="a", description="do A")],
            total_estimated_minutes=120,
            version="2.0",
            metadata={"agent": "windsurf", "duration": 5.2},
        )
        assert dr.total_estimated_minutes == 120
        assert dr.version == "2.0"
        assert dr.metadata["agent"] == "windsurf"

    def test_backward_compatible_no_new_fields(self):
        """Old JSON without new fields should parse fine."""
        data = {"sub_tasks": [{"id": "a", "description": "do A"}], "reasoning": "simple"}
        dr = DecomposeResult(**data)
        assert dr.version == "1.0"
        assert dr.total_estimated_minutes == 0


class TestTaskParentId:
    """Task 12: Verify Task.parent_task_id field."""

    def test_parent_task_id_none(self):
        t = Task(task_id="task-child-01", trace_id="a" * 16, skill_id="code-implement")
        assert t.parent_task_id is None

    def test_parent_task_id_set(self):
        t = Task(
            task_id="task-child-01",
            trace_id="a" * 16,
            skill_id="code-implement",
            parent_task_id="task-parent-01",
        )
        assert t.parent_task_id == "task-parent-01"


class TestSchemaValidation:
    """Task 45: Schema validation boundary tests."""

    def test_task_id_min_length(self):
        t = Task(task_id="t-a", trace_id="a" * 16, skill_id="code-implement")
        assert t.task_id == "t-a"

    def test_task_id_max_length(self):
        long_id = "task-" + "a" * 59
        t = Task(task_id=long_id, trace_id="a" * 16, skill_id="code-implement")
        assert t.task_id == long_id

    def test_builder_output_extra_fields(self):
        o = BuilderOutput(
            status="completed", summary="done",
            changed_files=[], check_results={},
            extra_field="allowed",
        )
        assert o.status == "completed"

    def test_reviewer_decision_enum(self):
        for d in ReviewDecision:
            o = ReviewerOutput(decision=d, summary="ok")
            assert o.decision == d

    def test_subtask_defaults(self):
        st = SubTask(id="test-sub", description="do something")
        assert st.deps == []
        assert st.skill_id == "code-implement"
        assert st.done_criteria == []

    def test_decompose_result_empty_sub_tasks(self):
        dr = DecomposeResult(sub_tasks=[])
        assert dr.sub_tasks == []
        assert dr.reasoning == ""

    def test_priority_all_values(self):
        values = [Priority.LOW, Priority.NORMAL, Priority.HIGH]
        assert len(values) == 3

    def test_skill_contract_supported_agents(self):
        data = {
            "id": "test", "version": "1.0.0",
            "compatibility": {"supported_agents": ["a", "b"]},
        }
        c = SkillContract.from_yaml(data)
        assert c.supported_agents == ["a", "b"]

    def test_skill_contract_no_compatibility(self):
        data = {"id": "test", "version": "1.0.0"}
        c = SkillContract.from_yaml(data)
        assert c.supported_agents == []


class TestModelConfigExtra:
    """Task 66: Pydantic model_config extra mode tests."""

    def test_task_forbids_extra_fields(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Task(
                task_id="task-test0001",
                trace_id="0" * 16,
                skill_id="code-implement",
                unknown_field="should fail",
            )

    def test_builder_output_allows_extra(self):
        bo = BuilderOutput(status="completed", summary="ok", extra_field="kept")
        assert bo.status == "completed"
        # extra field should be accessible
        assert bo.extra_field == "kept"  # type: ignore[attr-defined]

    def test_reviewer_output_allows_extra(self):
        ro = ReviewerOutput(decision="approve", extra="data")
        assert ro.decision == ReviewDecision.APPROVE
        assert ro.extra == "data"  # type: ignore[attr-defined]

    def test_subtask_ignores_extra(self):
        st = SubTask(id="test-sub", description="desc", bonus_field="ignored")
        assert st.id == "test-sub"
        assert not hasattr(st, "bonus_field")

    def test_decompose_result_ignores_extra(self):
        dr = DecomposeResult(
            sub_tasks=[SubTask(id="a", description="do A")],
            extra_stuff="ignored",
        )
        assert len(dr.sub_tasks) == 1
        assert not hasattr(dr, "extra_stuff")
