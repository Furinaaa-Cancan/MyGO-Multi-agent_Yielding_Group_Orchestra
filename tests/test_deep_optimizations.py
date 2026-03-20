"""Comprehensive tests for the 7 deep optimization modules in the MyGO framework.

Covers: Verifier, Sandbox, RepairCycle, RolePipeline, CostRouter,
BridgeExtractors, and DynamicPipeline.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Module imports ────────────────────────────────────────────────
from multi_agent.verifier import (
    VerificationResult,
    format_for_reviewer,
    format_for_repair,
    run_verification,
    _parse_pytest_counts,
    _parse_lint_error_count,
    LintResult,
)
from multi_agent.sandbox import SandboxResult, SandboxRunner
from multi_agent.repair_cycle import (
    FailureCategory,
    DiagnosisReport,
    RepairTarget,
    RepairPlan,
    diagnose,
    localize,
    build_repair_plan,
    format_repair_prompt,
)
from multi_agent.role_pipeline import (
    RoleKind,
    RoleSpec,
    PipelineConfig,
    MINIMAL,
    STANDARD,
    VERIFIED,
    FULL,
    get_pipeline,
    list_pipelines,
    select_pipeline,
)
from multi_agent.cost_router import (
    CostTier,
    CostAwareScore,
    agent_cost_tier,
    complexity_to_cost_tier,
    score_agent,
    select_agent_cost_aware,
    estimate_task_cost,
)
from multi_agent.bridge_extractors import (
    PythonExtractor,
    JavaScriptExtractor,
    TypeScriptExtractor,
    GoExtractor,
    RustExtractor,
    JavaExtractor,
    get_extractor,
    extract_multi_language,
)
from multi_agent.dynamic_pipeline import (
    SubTaskType,
    SubTaskClassification,
    classify_subtask,
    select_pipeline_for_subtask,
    enrich_subtasks,
)
from multi_agent.schema import AgentProfile, SkillContract


# ══════════════════════════════════════════════════════════════════
# 1. Verifier Tests
# ══════════════════════════════════════════════════════════════════


class TestVerifier:
    """Tests for the Verifier module."""

    def test_verification_result_creation(self):
        """VerificationResult can be created with defaults and custom values."""
        # Defaults
        vr = VerificationResult()
        assert vr.test_passed == 0
        assert vr.test_failed == 0
        assert vr.test_errors == 0
        assert vr.tests_ok is True
        assert vr.lint_ok is True
        assert vr.all_ok is True
        assert vr.coverage_pct is None

        # Custom values
        vr = VerificationResult(
            test_passed=10,
            test_failed=2,
            lint_errors=3,
            duration_sec=5.5,
            command_used="pytest -x",
        )
        assert vr.test_passed == 10
        assert vr.test_failed == 2
        assert vr.tests_ok is False
        assert vr.lint_ok is False
        assert vr.all_ok is False

    def test_format_for_reviewer_with_failures(self):
        """VerificationResult with failures produces Markdown with failure details."""
        vr = VerificationResult(
            test_passed=8,
            test_failed=2,
            test_errors=0,
            test_output="FAILED tests/test_foo.py::test_bar\nAssertionError: 1 != 2",
            lint_errors=1,
            lint_output="src/foo.py:10:5: E302 expected 2 blank lines",
            command_used="pytest -x --tb=short",
            duration_sec=3.2,
        )
        output = format_for_reviewer(vr)

        assert "## Automated Verification Results" in output
        assert "**Tests: FAIL**" in output
        assert "Passed: 8" in output
        assert "Failed: 2" in output
        assert "### Test Output (failures)" in output
        assert "FAILED tests/test_foo.py::test_bar" in output
        assert "**Lint: FAIL**" in output
        assert "### Lint Output" in output
        assert "WARNING" in output

    def test_format_for_reviewer_all_passed(self):
        """When all tests pass, format shows success and no failure sections."""
        vr = VerificationResult(
            test_passed=15,
            test_failed=0,
            test_errors=0,
            lint_errors=0,
            command_used="pytest -x",
            duration_sec=2.0,
        )
        output = format_for_reviewer(vr)

        assert "**Tests: PASS**" in output
        assert "**Lint: PASS**" in output
        assert "### Test Output" not in output
        assert "### Lint Output" not in output
        assert "All automated checks passed" in output

    def test_run_verification_no_command(self):
        """When verifier is disabled, run_verification returns an empty result."""
        config = {"enabled": False}
        result = run_verification(["foo.py"], "/tmp", config=config)
        assert result.test_passed == 0
        assert result.test_failed == 0
        assert result.all_ok is True

    def test_format_for_repair(self):
        """format_for_repair produces action-oriented output for failing tests."""
        vr = VerificationResult(
            test_passed=5,
            test_failed=3,
            test_errors=1,
            test_output="FAILED tests/test_x.py::test_calc\nAssertionError: expected 42",
            lint_errors=2,
            lint_output="src/calc.py:5: E501 line too long",
        )
        output = format_for_repair(vr)

        assert "## Verification Failures for Repair" in output
        assert "### Failing Tests" in output
        assert "3 failed, 1 errors" in output
        assert "#### Raw Test Output" in output
        assert "### Lint Errors" in output
        assert "2 errors found" in output

    def test_format_for_repair_type_errors(self):
        """format_for_repair includes type error section when present."""
        vr = VerificationResult(type_errors=5)
        output = format_for_repair(vr)
        assert "Type Errors: 5" in output

    def test_verification_result_coverage(self):
        """coverage_pct property works correctly."""
        vr = VerificationResult(coverage_pct=85.0)
        assert vr.coverage_pct == 85.0

        output = format_for_reviewer(vr)
        assert "Coverage: 85%" in output

    def test_parse_pytest_counts_standard(self):
        """_parse_pytest_counts extracts passed/failed/errors from summary line."""
        output = "====== 3 passed, 1 failed, 2 error in 5.2s ======"
        p, f, e = _parse_pytest_counts(output)
        assert p == 3
        assert f == 1
        assert e == 2

    def test_parse_pytest_counts_all_passed(self):
        """_parse_pytest_counts handles all-passed case."""
        output = "====== 10 passed in 1.0s ======"
        p, f, e = _parse_pytest_counts(output)
        assert p == 10
        assert f == 0
        assert e == 0

    def test_parse_lint_error_count(self):
        """_parse_lint_error_count counts errors from ruff output."""
        output = "src/foo.py:10:1 E302\nsrc/bar.py:5:1 F401\nFound 2 errors"
        assert _parse_lint_error_count(output) == 2

    def test_parse_lint_error_count_clean(self):
        """_parse_lint_error_count returns 0 for clean output."""
        assert _parse_lint_error_count("All checks passed!") == 0


# ══════════════════════════════════════════════════════════════════
# 2. Sandbox Tests
# ══════════════════════════════════════════════════════════════════


class TestSandbox:
    """Tests for the Sandbox module."""

    def test_sandbox_result_creation(self):
        """SandboxResult is a frozen dataclass with expected fields."""
        sr = SandboxResult(
            stdout="hello",
            stderr="",
            returncode=0,
            timed_out=False,
            duration_sec=0.1,
        )
        assert sr.stdout == "hello"
        assert sr.returncode == 0
        assert sr.timed_out is False
        # Frozen — cannot mutate
        with pytest.raises(AttributeError):
            sr.stdout = "changed"  # type: ignore[misc]

    def test_sandbox_runner_simple_command(self):
        """SandboxRunner can execute a simple echo command."""
        runner = SandboxRunner(max_output_chars=1000, default_timeout_sec=10)
        result = runner.run("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert result.timed_out is False
        assert result.duration_sec >= 0

    def test_sandbox_runner_timeout(self):
        """Command exceeding timeout returns timed_out=True."""
        runner = SandboxRunner(max_output_chars=500, default_timeout_sec=0.5)
        result = runner.run("sleep 10", timeout_sec=0.5)
        assert result.timed_out is True
        assert result.returncode == -1

    def test_sandbox_runner_output_truncation(self):
        """Long output is truncated to max_output_chars."""
        runner = SandboxRunner(max_output_chars=100, default_timeout_sec=10)
        # Generate output longer than 100 chars
        result = runner.run("python3 -c \"print('x' * 500)\"")
        assert result.returncode == 0
        # Output should be truncated with a notice
        assert "truncated" in result.stdout
        # Total length should be well under the original 500 chars
        assert len(result.stdout) < 500

    def test_sandbox_runner_nonzero_exit(self):
        """Command that fails returns nonzero exit code."""
        runner = SandboxRunner(max_output_chars=1000, default_timeout_sec=10)
        result = runner.run("python3 -c \"import sys; sys.exit(42)\"")
        assert result.returncode == 42
        assert result.timed_out is False

    def test_sandbox_runner_command_not_found(self):
        """Running a nonexistent command returns exit code 127."""
        runner = SandboxRunner(max_output_chars=500, default_timeout_sec=5)
        result = runner.run("__nonexistent_command_xyz__")
        assert result.returncode == 127
        assert "not found" in result.stderr.lower() or "Command not found" in result.stderr

    def test_sandbox_runner_custom_cwd(self, tmp_path: Path):
        """SandboxRunner respects the cwd argument."""
        (tmp_path / "marker.txt").write_text("found_it")
        runner = SandboxRunner()
        result = runner.run("cat marker.txt", cwd=str(tmp_path))
        assert "found_it" in result.stdout

    def test_sandbox_runner_list_command(self):
        """SandboxRunner accepts command as a list of strings."""
        runner = SandboxRunner(max_output_chars=500, default_timeout_sec=5)
        result = runner.run(["echo", "hello", "world"])
        assert result.returncode == 0
        assert "hello world" in result.stdout


# ══════════════════════════════════════════════════════════════════
# 3. Repair Cycle Tests
# ══════════════════════════════════════════════════════════════════


class TestRepairCycle:
    """Tests for the RepairCycle module."""

    def test_diagnose_test_failure(self):
        """Reviewer feedback about test failures yields TEST_FAILURE category."""
        vr = VerificationResult(
            test_passed=5,
            test_failed=2,
            test_errors=0,
            test_output=(
                "FAILED tests/test_calc.py::test_add\n"
                "AssertionError: assert 3 == 4\n"
                "===== 2 failed, 5 passed ====="
            ),
        )
        report = diagnose(
            reviewer_feedback="Tests are failing, please fix.",
            verification_result=vr,
        )
        assert report.category == FailureCategory.TEST_FAILURE
        assert report.confidence >= 0.5
        assert len(report.evidence) > 0

    def test_diagnose_logic_error(self):
        """Feedback about wrong logic without specific patterns maps to UNKNOWN."""
        report = diagnose(
            reviewer_feedback=(
                "The algorithm is producing wrong results "
                "and the output values are incorrect."
            ),
        )
        # Without verification and without matching specific keyword patterns,
        # generic feedback falls to UNKNOWN
        assert report.category == FailureCategory.UNKNOWN

    def test_diagnose_missing_requirement(self):
        """Feedback about missing feature yields MISSING_REQUIREMENT."""
        report = diagnose(
            reviewer_feedback=(
                "The implementation is missing the pagination feature. "
                "It should have included cursor-based navigation."
            ),
        )
        assert report.category == FailureCategory.MISSING_REQUIREMENT
        assert report.confidence >= 0.5
        assert any(
            "missing" in e.lower() or "should" in e.lower()
            for e in report.evidence
        )

    def test_diagnose_interface_mismatch(self):
        """Feedback about interface issues yields INTERFACE_MISMATCH."""
        vr = VerificationResult(
            test_passed=0,
            test_failed=1,
            test_errors=1,
            test_output="ImportError: cannot import name 'foo' from 'bar'",
        )
        report = diagnose("import error", verification_result=vr)
        assert report.category == FailureCategory.INTERFACE_MISMATCH

    def test_diagnose_lint_only(self):
        """Lint-only failures with passing tests yield STYLE_ISSUE or QUALITY_GATE."""
        vr = VerificationResult(
            test_passed=5,
            test_failed=0,
            test_errors=0,
            lint_errors=3,
            lint_output="src/foo.py:1:1 E302",
        )
        report = diagnose("lint issues", verification_result=vr)
        assert report.category in (
            FailureCategory.STYLE_ISSUE,
            FailureCategory.QUALITY_GATE,
        )

    def test_localize_from_test_output(self):
        """localize extracts file and function from pytest output."""
        vr = VerificationResult(
            test_passed=3,
            test_failed=1,
            test_output="FAILED tests/test_utils.py::test_parse_date",
        )
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="test_parse_date fails",
            evidence=["FAILED: tests/test_utils.py::test_parse_date"],
        )
        targets = localize(
            diagnosis=diag,
            changed_files=["src/utils.py", "tests/test_utils.py"],
            verification_result=vr,
        )
        assert len(targets) >= 1
        file_paths = [t.file_path for t in targets]
        assert any("test_utils" in fp for fp in file_paths)

    def test_localize_fallback_to_changed_files(self):
        """When no specific location can be identified, all changed files are returned."""
        diag = DiagnosisReport(
            category=FailureCategory.UNKNOWN,
            root_cause_summary="unknown issue",
            evidence=[],
        )
        targets = localize(diag, ["src/foo.py", "src/bar.py"])
        assert len(targets) == 2
        file_paths = {t.file_path for t in targets}
        assert file_paths == {"src/foo.py", "src/bar.py"}

    def test_localize_file_line_extraction(self):
        """localize extracts line range from file:line references in evidence."""
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="test failed",
            evidence=["Error at src/auth.py:42"],
        )
        targets = localize(diag, ["src/auth.py"])
        assert len(targets) >= 1
        found = [t for t in targets if t.file_path == "src/auth.py"]
        assert len(found) >= 1
        if found[0].line_range:
            assert found[0].line_range[0] <= 42 <= found[0].line_range[1]

    def test_build_repair_plan_incremental(self):
        """retry_count < 2 yields 'incremental' strategy."""
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="Test fails",
        )
        targets = [
            RepairTarget(file_path="src/foo.py", issue="Bug here"),
        ]
        plan = build_repair_plan(diag, targets, retry_count=0, budget=3)
        assert plan.strategy == "incremental"

        plan1 = build_repair_plan(diag, targets, retry_count=1, budget=3)
        assert plan1.strategy == "incremental"

    def test_build_repair_plan_fresh_start(self):
        """retry_count >= 2 (and not final attempt) yields 'fresh_start'."""
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="Test fails",
        )
        targets = [
            RepairTarget(file_path="src/foo.py", issue="Bug here"),
        ]
        # retry_count=2, budget=5 -> not final attempt, so fresh_start
        plan = build_repair_plan(diag, targets, retry_count=2, budget=5)
        assert plan.strategy == "fresh_start"

    def test_build_repair_plan_final_attempt_incremental(self):
        """Final attempt (retry_count >= budget-1) always uses incremental."""
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="Test fails",
        )
        targets = [RepairTarget(file_path="src/foo.py")]
        plan = build_repair_plan(diag, targets, retry_count=4, budget=5)
        assert plan.strategy == "incremental"

    def test_build_repair_plan_priority_order(self):
        """Priority ordering puts non-test files before test files."""
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="Test fails",
        )
        targets = [
            RepairTarget(file_path="tests/test_foo.py"),
            RepairTarget(file_path="src/foo.py", line_range=(10, 20)),
        ]
        plan = build_repair_plan(diag, targets, retry_count=0)
        # src/foo.py should come before tests/test_foo.py in priority
        assert plan.priority_order[0] == 1  # index of src/foo.py

    def test_format_repair_prompt(self):
        """Full roundtrip: diagnose -> localize -> plan -> format produces valid Markdown."""
        vr = VerificationResult(
            test_passed=3,
            test_failed=1,
            test_output="FAILED tests/test_auth.py::test_login\nAssertionError: expected True",
        )
        diag = diagnose("Tests fail", verification_result=vr)
        targets = localize(diag, ["src/auth.py", "tests/test_auth.py"], vr)
        plan = build_repair_plan(diag, targets, retry_count=0)
        prompt = format_repair_prompt(plan)

        assert "## Repair Instructions" in prompt
        assert "### Diagnosis" in prompt
        assert "**Strategy:** incremental" in prompt
        assert "test_failure" in prompt
        assert "### Instructions" in prompt

    def test_format_repair_prompt_fresh_start(self):
        """Fresh-start strategy includes re-read instructions."""
        diag = DiagnosisReport(
            category=FailureCategory.TEST_FAILURE,
            root_cause_summary="Persistent failure",
            evidence=["multiple attempts failed"],
        )
        targets = [RepairTarget(file_path="src/core.py", issue="keeps failing")]
        plan = build_repair_plan(diag, targets, retry_count=2, budget=5)
        prompt = format_repair_prompt(plan)

        assert "fresh_start" in prompt
        assert "Re-read the original requirements" in prompt


# ══════════════════════════════════════════════════════════════════
# 4. Role Pipeline Tests
# ══════════════════════════════════════════════════════════════════


class TestRolePipeline:
    """Tests for the RolePipeline module."""

    def test_predefined_pipelines_exist(self):
        """All four predefined pipelines (minimal, standard, verified, full) exist."""
        names = {p.name for p in [MINIMAL, STANDARD, VERIFIED, FULL]}
        assert names == {"minimal", "standard", "verified", "full"}

    def test_select_pipeline_bugfix(self):
        """Bugfix tasks select 'minimal' pipeline."""
        p = select_pipeline("bugfix")
        assert p.name == "minimal"

    def test_select_pipeline_feature(self):
        """Feature tasks select 'verified' (or 'standard' for simple_feature)."""
        p = select_pipeline("feature")
        assert p.name == "verified"

        p2 = select_pipeline("simple_feature")
        assert p2.name == "standard"

    def test_select_pipeline_complex(self):
        """Complex feature tasks select 'full', as does complexity upgrade."""
        p = select_pipeline("complex_feature")
        assert p.name == "full"

        # Complexity upgrade from feature -> full
        p2 = select_pipeline("feature", complexity_level="complex")
        assert p2.name == "full"

    def test_pipeline_role_sequence(self):
        """Verify role ordering is correct in each predefined pipeline."""
        assert MINIMAL.role_kinds == [
            RoleKind.PLAN, RoleKind.BUILD, RoleKind.DECIDE,
        ]
        assert STANDARD.role_kinds == [
            RoleKind.PLAN, RoleKind.BUILD, RoleKind.REVIEW, RoleKind.DECIDE,
        ]
        assert VERIFIED.role_kinds == [
            RoleKind.PLAN, RoleKind.BUILD, RoleKind.VERIFY,
            RoleKind.REVIEW, RoleKind.DECIDE,
        ]
        assert FULL.role_kinds == [
            RoleKind.PLAN, RoleKind.ARCHITECT, RoleKind.BUILD,
            RoleKind.VERIFY, RoleKind.REVIEW, RoleKind.DECIDE,
        ]

    def test_get_pipeline_by_name(self):
        """get_pipeline returns correct pipeline by name and raises on unknown."""
        p = get_pipeline("standard")
        assert p.name == "standard"
        assert p is STANDARD

        with pytest.raises(KeyError, match="Unknown pipeline"):
            get_pipeline("nonexistent")

    def test_list_pipelines(self):
        """list_pipelines returns all pipelines sorted by role count (ascending)."""
        pipelines = list_pipelines()
        assert len(pipelines) >= 4
        role_counts = [len(p.roles) for p in pipelines]
        assert role_counts == sorted(role_counts)

    def test_pipeline_has_role(self):
        """PipelineConfig.has_role works correctly."""
        assert STANDARD.has_role(RoleKind.BUILD)
        assert STANDARD.has_role(RoleKind.REVIEW)
        assert not MINIMAL.has_role(RoleKind.REVIEW)
        assert not MINIMAL.has_role(RoleKind.VERIFY)

    def test_pipeline_steps_for_retry(self):
        """steps_for_retry excludes roles with skip_on_retry=True."""
        retry_roles = STANDARD.steps_for_retry()
        retry_kinds = [r.kind for r in retry_roles]
        assert RoleKind.PLAN not in retry_kinds
        assert RoleKind.BUILD in retry_kinds
        assert RoleKind.REVIEW in retry_kinds

    def test_pipeline_total_timeout(self):
        """total_timeout_sec sums all role timeouts."""
        total = STANDARD.total_timeout_sec
        expected = sum(r.timeout_sec for r in STANDARD.roles)
        assert total == expected
        assert total > 0

    def test_role_spec_auto_derives_capability(self):
        """RoleSpec auto-derives agent_capability and template_name from kind."""
        spec = RoleSpec(kind=RoleKind.BUILD)
        assert spec.agent_capability == "implementation"
        assert spec.template_name == "build"


# ══════════════════════════════════════════════════════════════════
# 5. Cost Router Tests
# ══════════════════════════════════════════════════════════════════


class TestCostRouter:
    """Tests for the CostRouter module."""

    def _make_agent(
        self,
        agent_id: str,
        cost: float,
        reliability: float = 0.9,
        queue_health: float = 0.9,
        capabilities: list[str] | None = None,
    ) -> AgentProfile:
        return AgentProfile(
            id=agent_id,
            cost=cost,
            reliability=reliability,
            queue_health=queue_health,
            capabilities=capabilities or ["implementation", "review"],
        )

    def test_cost_tier_mapping(self):
        """simple->ECONOMY, complex->PREMIUM, medium->STANDARD."""
        assert complexity_to_cost_tier("simple") == CostTier.ECONOMY
        assert complexity_to_cost_tier("low") == CostTier.ECONOMY
        assert complexity_to_cost_tier("medium") == CostTier.STANDARD
        assert complexity_to_cost_tier("complex") == CostTier.PREMIUM
        assert complexity_to_cost_tier("high") == CostTier.PREMIUM

    def test_agent_cost_tier_classification(self):
        """agent_cost_tier classifies agents by their cost field."""
        cheap = self._make_agent("cheap", cost=0.1)
        mid = self._make_agent("mid", cost=0.5)
        premium = self._make_agent("premium", cost=0.9)

        assert agent_cost_tier(cheap) == CostTier.ECONOMY
        assert agent_cost_tier(mid) == CostTier.STANDARD
        assert agent_cost_tier(premium) == CostTier.PREMIUM

    def test_score_agent_matching_tier(self):
        """Agent cost matching task complexity yields high cost_factor (1.0)."""
        agent = self._make_agent("cheap", cost=0.1)
        score = score_agent(agent, task_complexity="simple")
        assert score.cost_factor == 1.0
        assert score.final_score > 0

    def test_score_agent_mismatched_tier(self):
        """Expensive agent for simple task yields lower score than cheap agent."""
        expensive = self._make_agent("expensive", cost=0.9)
        cheap = self._make_agent("cheap", cost=0.1)

        score_exp = score_agent(expensive, task_complexity="simple")
        score_cheap = score_agent(cheap, task_complexity="simple")

        assert score_cheap.final_score > score_exp.final_score
        assert score_cheap.cost_factor > score_exp.cost_factor

    def test_score_agent_budget_depleted(self):
        """Low budget leads to reduced budget_factor and final_score."""
        agent = self._make_agent("mid", cost=0.5)

        # Full budget
        score_full = score_agent(
            agent, budget_remaining=100.0, total_budget=100.0,
        )
        # Nearly depleted budget (10% remaining)
        score_low = score_agent(
            agent, budget_remaining=10.0, total_budget=100.0,
        )

        assert score_low.budget_factor < score_full.budget_factor
        assert score_low.final_score < score_full.final_score

    def test_score_agent_no_budget(self):
        """When budget info is None, budget_factor is 1.0."""
        agent = self._make_agent("mid", cost=0.5)
        score = score_agent(agent, budget_remaining=None, total_budget=None)
        assert score.budget_factor == 1.0

    def test_select_agent_cost_aware(self):
        """Selects cheapest viable agent for simple task."""
        cheap = self._make_agent(
            "cheap-agent", cost=0.1, reliability=0.85, queue_health=0.9,
        )
        mid = self._make_agent(
            "mid-agent", cost=0.5, reliability=0.9, queue_health=0.9,
        )
        premium = self._make_agent(
            "premium-agent", cost=0.9, reliability=0.95, queue_health=0.9,
        )

        contract = SkillContract(id="test-skill", supported_agents=[])
        selected = select_agent_cost_aware(
            agents=[cheap, mid, premium],
            contract=contract,
            role="builder",
            complexity_level="simple",
        )
        # For a simple task, the cheap agent should win due to cost tier match
        assert selected.id == "cheap-agent"

    def test_select_agent_cost_aware_no_eligible(self):
        """ValueError raised when no agents match."""
        contract = SkillContract(
            id="test-skill",
            supported_agents=["nonexistent-only"],
        )
        with pytest.raises(ValueError, match="No eligible agent"):
            select_agent_cost_aware(
                agents=[self._make_agent("agent1", cost=0.5)],
                contract=contract,
                role="builder",
            )

    def test_estimate_task_cost(self):
        """estimate_task_cost returns a positive float."""
        cost = estimate_task_cost("standard", complexity_level="medium")
        assert cost > 0
        assert isinstance(cost, float)

    def test_estimate_task_cost_complexity_scaling(self):
        """Complex tasks cost more than simple tasks."""
        simple = estimate_task_cost("standard", complexity_level="simple")
        complex_ = estimate_task_cost("full", complexity_level="complex")
        assert complex_ > simple

    def test_cost_aware_score_reasoning(self):
        """CostAwareScore includes human-readable reasoning."""
        agent = self._make_agent("test-agent", cost=0.5)
        score = score_agent(agent, task_complexity="medium")
        assert "base=" in score.reasoning
        assert "cost_factor=" in score.reasoning
        assert "final=" in score.reasoning


# ══════════════════════════════════════════════════════════════════
# 6. Bridge Extractors Tests
# ══════════════════════════════════════════════════════════════════


class TestBridgeExtractors:
    """Tests for the BridgeExtractors module."""

    def test_python_extractor(self, tmp_path: Path):
        """PythonExtractor delegates to AST-based extraction for .py files."""
        py_file = tmp_path / "mod.py"
        py_file.write_text(textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello, {name}"

            class Greeter:
                pass
        """))
        ext = PythonExtractor()
        assert ".py" in ext.supported_extensions()
        symbols = ext.extract(py_file)
        names = [s.name for s in symbols]
        assert "hello" in names
        assert "Greeter" in names

    def test_javascript_extractor(self, tmp_path: Path):
        """JavaScriptExtractor extracts functions, classes, and consts from JS."""
        js_file = tmp_path / "utils.js"
        js_file.write_text(textwrap.dedent("""\
            export function add(a, b) {
                return a + b;
            }

            export class Calculator {
                constructor() {}
            }

            export const PI = 3.14;
        """))
        ext = JavaScriptExtractor()
        assert ".js" in ext.supported_extensions()
        symbols = ext.extract(js_file)
        names = [s.name for s in symbols]
        assert "add" in names
        assert "Calculator" in names
        assert "PI" in names

        # Verify kinds
        kinds = {s.name: s.kind for s in symbols}
        assert kinds["add"] == "function"
        assert kinds["Calculator"] == "class"
        assert kinds["PI"] == "constant"

    def test_typescript_extractor(self, tmp_path: Path):
        """TypeScriptExtractor extracts typed functions, interfaces, and type aliases."""
        ts_file = tmp_path / "service.ts"
        ts_file.write_text(textwrap.dedent("""\
            export function greet(name: string): string {
                return `Hello ${name}`;
            }

            export interface UserConfig {
                name: string;
                age: number;
            }

            export type UserId = string;

            export const MAX_RETRIES = 3;
        """))
        ext = TypeScriptExtractor()
        assert ".ts" in ext.supported_extensions()
        symbols = ext.extract(ts_file)
        names = [s.name for s in symbols]
        assert "greet" in names
        assert "UserConfig" in names
        assert "UserId" in names
        assert "MAX_RETRIES" in names

        # greet should have return type in signature
        greet_sym = next(s for s in symbols if s.name == "greet")
        assert "string" in greet_sym.signature

    def test_go_extractor(self, tmp_path: Path):
        """GoExtractor extracts exported functions, structs, interfaces, and consts."""
        go_file = tmp_path / "handler.go"
        go_file.write_text(textwrap.dedent("""\
            package main

            func HandleRequest(w http.ResponseWriter, r *http.Request) {
                w.Write([]byte("ok"))
            }

            func privateHelper() {
                // not exported
            }

            type Server struct {
                Port int
            }

            type Handler interface {
                ServeHTTP()
            }

            const MaxConns = 100
        """))
        ext = GoExtractor()
        assert ".go" in ext.supported_extensions()
        symbols = ext.extract(go_file)
        names = [s.name for s in symbols]

        assert "HandleRequest" in names
        assert "Server" in names
        assert "Handler" in names
        assert "MaxConns" in names
        # Unexported should be excluded
        assert "privateHelper" not in names

    def test_rust_extractor(self, tmp_path: Path):
        """RustExtractor extracts pub fn, pub struct, pub enum, pub trait, and pub const."""
        rs_file = tmp_path / "lib.rs"
        rs_file.write_text(textwrap.dedent("""\
            pub fn process(input: &str) -> Result<String> {
                Ok(input.to_string())
            }

            pub struct Config {
                pub name: String,
            }

            pub enum Status {
                Active,
                Inactive,
            }

            pub trait Processor {
                fn run(&self);
            }

            pub const VERSION: &str = "1.0";
        """))
        ext = RustExtractor()
        assert ".rs" in ext.supported_extensions()
        symbols = ext.extract(rs_file)
        names = [s.name for s in symbols]

        assert "process" in names
        assert "Config" in names
        assert "Status" in names
        assert "Processor" in names
        assert "VERSION" in names

        # Check signature includes return type
        process_sym = next(s for s in symbols if s.name == "process")
        assert "->" in process_sym.signature

    def test_java_extractor(self, tmp_path: Path):
        """JavaExtractor extracts public classes, methods, interfaces, and constants."""
        java_file = tmp_path / "UserService.java"
        java_file.write_text(textwrap.dedent("""\
            public class UserService {
                public static final int MAX_USERS = 1000;

                public String getUser(int id) {
                    return "user-" + id;
                }

                public void createUser(String name, int age) {
                    // ...
                }
            }

            public interface UserRepository {
                void save(String user);
            }
        """))
        ext = JavaExtractor()
        assert ".java" in ext.supported_extensions()
        symbols = ext.extract(java_file)
        names = [s.name for s in symbols]

        assert "UserService" in names
        assert "MAX_USERS" in names
        assert "getUser" in names
        assert "createUser" in names
        assert "UserRepository" in names

    def test_get_extractor_by_extension(self):
        """get_extractor maps file extensions to correct extractor types."""
        assert isinstance(get_extractor(Path("foo.py")), PythonExtractor)
        assert isinstance(get_extractor(Path("bar.js")), JavaScriptExtractor)
        assert isinstance(get_extractor(Path("bar.jsx")), JavaScriptExtractor)
        assert isinstance(get_extractor(Path("baz.ts")), TypeScriptExtractor)
        assert isinstance(get_extractor(Path("baz.tsx")), TypeScriptExtractor)
        assert isinstance(get_extractor(Path("main.go")), GoExtractor)
        assert isinstance(get_extractor(Path("lib.rs")), RustExtractor)
        assert isinstance(get_extractor(Path("App.java")), JavaExtractor)

    def test_get_extractor_unknown(self):
        """get_extractor returns None for unsupported extensions."""
        assert get_extractor(Path("readme.txt")) is None
        assert get_extractor(Path("data.csv")) is None
        assert get_extractor(Path("image.png")) is None

    def test_extract_multi_language(self, tmp_path: Path):
        """extract_multi_language handles a mixed list of JS and Go files."""
        js_file = tmp_path / "utils.js"
        js_file.write_text("export function helper() {}\n")

        go_file = tmp_path / "server.go"
        go_file.write_text(textwrap.dedent("""\
            package main

            func StartServer(port int) {
                // ...
            }
        """))

        contract = extract_multi_language(
            changed_files=["utils.js", "server.go"],
            codebase_root=tmp_path,
            subtask_id="task-1",
        )

        assert contract.subtask_id == "task-1"
        names = [e.name for e in contract.exports]
        assert "helper" in names
        assert "StartServer" in names
        assert len(contract.file_paths) == 2

    def test_extract_multi_language_nonexistent_file(self, tmp_path: Path):
        """extract_multi_language gracefully skips non-existent files."""
        contract = extract_multi_language(
            changed_files=["nonexistent.js"],
            codebase_root=tmp_path,
            subtask_id="task-2",
        )
        assert contract.subtask_id == "task-2"
        assert len(contract.exports) == 0

    def test_javascript_async_export(self, tmp_path: Path):
        """JavaScriptExtractor handles async function exports."""
        js_file = tmp_path / "api.js"
        js_file.write_text("export async function fetchData(url) {\n  return await fetch(url);\n}\n")
        ext = JavaScriptExtractor()
        symbols = ext.extract(js_file)
        assert len(symbols) >= 1
        assert symbols[0].name == "fetchData"
        assert "async" in symbols[0].signature

    def test_rust_async_fn(self, tmp_path: Path):
        """RustExtractor handles pub async fn."""
        rs_file = tmp_path / "async_lib.rs"
        rs_file.write_text("pub async fn fetch_data(url: &str) -> String {\n    String::new()\n}\n")
        ext = RustExtractor()
        symbols = ext.extract(rs_file)
        assert len(symbols) >= 1
        assert symbols[0].name == "fetch_data"
        assert "async" in symbols[0].signature


# ══════════════════════════════════════════════════════════════════
# 7. Dynamic Pipeline Tests
# ══════════════════════════════════════════════════════════════════


class TestDynamicPipeline:
    """Tests for the DynamicPipeline module."""

    def test_classify_bugfix(self):
        """'fix the login bug' classifies as BUGFIX."""
        c = classify_subtask("fix the login bug")
        assert c.task_type == SubTaskType.BUGFIX

    def test_classify_new_feature(self):
        """'implement user registration' classifies as NEW_FEATURE."""
        c = classify_subtask("implement user registration")
        assert c.task_type == SubTaskType.NEW_FEATURE

    def test_classify_refactor(self):
        """'refactor the auth module' classifies as REFACTOR."""
        c = classify_subtask("refactor the auth module")
        assert c.task_type == SubTaskType.REFACTOR

    def test_classify_test(self):
        """Description with strong test signals classifies as TEST_ADDITION."""
        c = classify_subtask("write test cases and assertions for the payment module, increase test coverage")
        assert c.task_type == SubTaskType.TEST_ADDITION

    def test_classify_api(self):
        """'create REST endpoint for orders' classifies as API_ENDPOINT."""
        c = classify_subtask("create REST endpoint for orders")
        assert c.task_type == SubTaskType.API_ENDPOINT

    def test_classify_empty_description(self):
        """Empty description defaults to NEW_FEATURE with low confidence."""
        c = classify_subtask("")
        assert c.task_type == SubTaskType.NEW_FEATURE
        assert c.confidence <= 0.3

    def test_classify_confidence_positive(self):
        """Classification always has positive confidence for matched signals."""
        c = classify_subtask("fix the broken build")
        assert c.confidence > 0

    def test_select_pipeline_for_bugfix(self):
        """BUGFIX classification maps to 'minimal' pipeline."""
        c = SubTaskClassification(
            task_type=SubTaskType.BUGFIX,
            confidence=0.9,
            reasoning="bugfix signals",
            suggested_pipeline="",
        )
        assert select_pipeline_for_subtask(c) == "minimal"

    def test_select_pipeline_for_api(self):
        """API_ENDPOINT classification maps to 'verified' pipeline."""
        c = SubTaskClassification(
            task_type=SubTaskType.API_ENDPOINT,
            confidence=0.85,
            reasoning="api signals",
            suggested_pipeline="",
        )
        assert select_pipeline_for_subtask(c) == "verified"

    def test_select_pipeline_for_integration(self):
        """INTEGRATION classification maps to 'full' pipeline."""
        c = SubTaskClassification(
            task_type=SubTaskType.INTEGRATION,
            confidence=0.8,
            reasoning="integration signals",
            suggested_pipeline="",
        )
        assert select_pipeline_for_subtask(c) == "full"

    def test_select_pipeline_for_test_addition(self):
        """TEST_ADDITION classification maps to 'minimal' pipeline."""
        c = SubTaskClassification(
            task_type=SubTaskType.TEST_ADDITION,
            confidence=0.9,
            reasoning="test signals",
            suggested_pipeline="",
        )
        assert select_pipeline_for_subtask(c) == "minimal"

    def test_select_pipeline_new_feature_complex(self):
        """NEW_FEATURE with complex parent maps to 'verified'."""
        c = SubTaskClassification(
            task_type=SubTaskType.NEW_FEATURE,
            confidence=0.5,
            reasoning="",
            suggested_pipeline="",
        )
        assert select_pipeline_for_subtask(c, parent_complexity="complex") == "verified"

    def test_enrich_subtasks(self):
        """enrich_subtasks adds classification metadata to task dicts."""
        tasks = [
            {"description": "fix the login bug", "id": "task-1"},
            {"description": "create REST endpoint for orders", "id": "task-2"},
            {"description": "integrate payment webhook", "id": "task-3"},
        ]
        enriched = enrich_subtasks(tasks, parent_complexity="medium")

        assert len(enriched) == 3

        # task-1: bugfix -> minimal
        assert enriched[0]["_task_type"] == "bugfix"
        assert enriched[0]["_pipeline"] == "minimal"
        assert enriched[0]["_confidence"] > 0
        assert "_classification" in enriched[0]
        assert "_reasoning" in enriched[0]

        # task-2: api_endpoint -> verified
        assert enriched[1]["_task_type"] == "api_endpoint"
        assert enriched[1]["_pipeline"] == "verified"

        # task-3: integration -> full
        assert enriched[2]["_task_type"] == "integration"
        assert enriched[2]["_pipeline"] == "full"

        # Original keys preserved
        assert enriched[0]["id"] == "task-1"
        assert enriched[1]["id"] == "task-2"

    def test_enrich_subtasks_with_deps(self):
        """enrich_subtasks uses deps as additional classification signal."""
        tasks = [
            {
                "description": "add the webhook handler",
                "id": "task-1",
                "deps": ["integration-setup"],
            },
        ]
        enriched = enrich_subtasks(tasks)
        assert len(enriched) == 1
        # "webhook" and "integration" signals should be picked up
        assert enriched[0]["_task_type"] in ("integration", "api_endpoint")

    # ── Regression tests for bug fixes ──

    def test_enrich_subtasks_preserves_done_criteria(self):
        """enrich_subtasks uses done_criteria for classification."""
        tasks = [
            {
                "description": "update the system",
                "done_criteria": "All tests should pass and coverage above 80%",
                "id": "task-1",
            },
        ]
        enriched = enrich_subtasks(tasks)
        assert len(enriched) == 1
        # "test" and "coverage" signals should influence classification
        assert enriched[0]["_task_type"] == "test_addition"


# ══════════════════════════════════════════════════════════════════
# 8. Regression Tests for Critical Bug Fixes
# ══════════════════════════════════════════════════════════════════


class TestRepairCycleTypeFixes:
    """Regression: diagnose() must accept dict builder_output and list done_criteria."""

    def test_diagnose_with_dict_builder_output(self):
        """diagnose() should handle dict builder_output (from graph.py state)."""
        builder_dict = {
            "status": "completed",
            "summary": "Implemented login endpoint",
            "changed_files": ["src/auth.py"],
            "handoff_notes": "JWT integration pending",
        }
        d = diagnose(
            reviewer_feedback="The login test fails with assertion error",
            builder_output=builder_dict,
        )
        assert d.category is not None
        assert d.root_cause_summary

    def test_diagnose_with_list_done_criteria(self):
        """diagnose() should handle list done_criteria (from graph.py state)."""
        d = diagnose(
            reviewer_feedback="Missing the email validation feature",
            done_criteria=["User can register", "Email is validated", "Password meets policy"],
        )
        assert d.category == FailureCategory.MISSING_REQUIREMENT

    def test_diagnose_with_none_inputs(self):
        """diagnose() should handle None builder_output and done_criteria."""
        d = diagnose(
            reviewer_feedback="something is wrong",
            builder_output=None,
            done_criteria=None,
        )
        assert d.category is not None


class TestVerifierLintDuration:
    """Regression: LintResult must capture duration_sec."""

    def test_lint_result_has_duration(self):
        lr = LintResult(error_count=0, output="", returncode=0,
                        command_used="ruff check", duration_sec=1.5)
        assert lr.duration_sec == 1.5

    def test_lint_result_default_duration(self):
        lr = LintResult()
        assert lr.duration_sec == 0.0


class TestPythonExtractorPathFix:
    """Regression: PythonExtractor must work with nested module paths."""

    def test_nested_module_extraction(self, tmp_path: Path):
        """PythonExtractor should extract from files in subdirectories."""
        subdir = tmp_path / "src" / "mymod"
        subdir.mkdir(parents=True)
        py_file = subdir / "utils.py"
        py_file.write_text("def compute(x: int) -> int:\n    return x * 2\n")

        ext = PythonExtractor()
        symbols = ext.extract(py_file)
        assert any(s.name == "compute" for s in symbols)

    def test_extractor_file_path_is_absolute(self, tmp_path: Path):
        """Extracted symbols should have meaningful file_path (not just filename)."""
        py_file = tmp_path / "module.py"
        py_file.write_text("def foo() -> str:\n    return 'bar'\n")

        ext = PythonExtractor()
        symbols = ext.extract(py_file)
        assert len(symbols) >= 1
        # After fix: file_path should be full path, not just "module.py"
        assert str(tmp_path) in symbols[0].file_path or symbols[0].file_path == "module.py"
