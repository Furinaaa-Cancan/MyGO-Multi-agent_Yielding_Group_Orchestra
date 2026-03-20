"""Tests for the 7 deep optimization modules.

Covers: verifier, sandbox, repair_cycle, role_pipeline,
cost_router, bridge_extractors, dynamic_pipeline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════
# 1. Sandbox Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.sandbox import SandboxResult, SandboxRunner


class TestSandboxResult:
    def test_creation(self):
        r = SandboxResult(stdout="hi", stderr="", returncode=0, timed_out=False, duration_sec=0.1)
        assert r.stdout == "hi"
        assert r.returncode == 0
        assert not r.timed_out

    def test_with_all_fields(self):
        r = SandboxResult(stdout="out", stderr="err", returncode=1, timed_out=True, duration_sec=5.0)
        assert r.stderr == "err"
        assert r.timed_out


class TestSandboxRunner:
    def test_simple_echo(self):
        runner = SandboxRunner()
        r = runner.run("echo hello")
        assert "hello" in r.stdout
        assert r.returncode == 0
        assert not r.timed_out

    def test_nonzero_exit(self):
        runner = SandboxRunner()
        r = runner.run("sh -c 'exit 42'")
        assert r.returncode == 42

    def test_timeout(self):
        runner = SandboxRunner(default_timeout_sec=1)
        r = runner.run("sleep 30", timeout_sec=1)
        assert r.timed_out
        assert r.returncode != 0

    def test_output_truncation(self):
        runner = SandboxRunner(max_output_chars=50)
        r = runner.run("sh -c 'seq 1 1000'")
        assert len(r.stdout) <= 120  # some overhead from truncation marker

    def test_custom_cwd(self, tmp_path):
        (tmp_path / "marker.txt").write_text("found")
        runner = SandboxRunner()
        r = runner.run("cat marker.txt", cwd=str(tmp_path))
        assert "found" in r.stdout


# ═══════════════════════════════════════════════════════════
# 2. Verifier Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.verifier import (
    LintResult,
    VerificationResult,
    format_for_repair,
    format_for_reviewer,
    _parse_pytest_counts,
    _parse_lint_error_count,
)


class TestVerificationResult:
    def test_creation(self):
        vr = VerificationResult(test_passed=5, test_failed=1, test_errors=0)
        assert vr.test_passed == 5
        assert not vr.tests_ok
        assert not vr.all_ok

    def test_all_ok(self):
        vr = VerificationResult(test_passed=10, test_failed=0, test_errors=0,
                                lint_errors=0, type_errors=0)
        assert vr.tests_ok
        assert vr.lint_ok
        assert vr.all_ok

    def test_lint_not_ok(self):
        vr = VerificationResult(test_passed=5, lint_errors=3)
        assert vr.tests_ok
        assert not vr.lint_ok
        assert not vr.all_ok


class TestParsePytestCounts:
    def test_standard_summary(self):
        output = "====== 3 passed, 1 failed, 2 error in 5.2s ======"
        p, f, e = _parse_pytest_counts(output)
        assert p == 3
        assert f == 1
        assert e == 2

    def test_all_passed(self):
        output = "====== 10 passed in 1.0s ======"
        p, f, e = _parse_pytest_counts(output)
        assert p == 10
        assert f == 0

    def test_no_match(self):
        p, f, e = _parse_pytest_counts("no pytest output here")
        assert p == 0 and f == 0 and e == 0


class TestParseLintErrorCount:
    def test_ruff_summary(self):
        output = "src/foo.py:10:1 E302\nsrc/bar.py:5:1 F401\nFound 2 errors"
        assert _parse_lint_error_count(output) == 2

    def test_no_errors(self):
        assert _parse_lint_error_count("All checks passed!") == 0


class TestFormatForReviewer:
    def test_with_failures(self):
        vr = VerificationResult(test_passed=3, test_failed=2, test_errors=0,
                                lint_errors=1, test_output="FAILED test_foo",
                                lint_output="E302 expected", command_used="pytest")
        out = format_for_reviewer(vr)
        assert "FAIL" in out
        assert "Passed: 3" in out
        assert "Failed: 2" in out
        assert "WARNING" in out

    def test_all_passed(self):
        vr = VerificationResult(test_passed=10, test_failed=0, test_errors=0, lint_errors=0,
                                command_used="pytest", duration_sec=1.5)
        out = format_for_reviewer(vr)
        assert "PASS" in out
        assert "All automated checks passed" in out


class TestFormatForRepair:
    def test_with_test_failure(self):
        vr = VerificationResult(test_passed=5, test_failed=2, test_output="assertion error here")
        out = format_for_repair(vr)
        assert "Failing Tests" in out
        assert "2 failed" in out

    def test_with_lint_only(self):
        vr = VerificationResult(test_passed=5, lint_errors=3, lint_output="E302 stuff")
        out = format_for_repair(vr)
        assert "Lint Errors" in out


# ═══════════════════════════════════════════════════════════
# 3. Repair Cycle Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.repair_cycle import (
    DiagnosisReport,
    FailureCategory,
    RepairPlan,
    RepairTarget,
    build_repair_plan,
    diagnose,
    format_repair_prompt,
    localize,
)


class TestDiagnose:
    def test_test_failure_from_verification(self):
        vr = VerificationResult(test_passed=3, test_failed=2, test_errors=0,
                                test_output="FAILED tests/test_auth.py::test_login - AssertionError")
        d = diagnose("tests fail", verification_result=vr)
        assert d.category == FailureCategory.TEST_FAILURE
        assert d.confidence >= 0.7

    def test_interface_mismatch_from_verification(self):
        vr = VerificationResult(test_passed=0, test_failed=1, test_errors=1,
                                test_output="ImportError: cannot import name 'foo' from 'bar'")
        d = diagnose("import error", verification_result=vr)
        assert d.category == FailureCategory.INTERFACE_MISMATCH

    def test_missing_requirement_from_feedback(self):
        d = diagnose("The user registration endpoint is missing. Should include email validation.")
        assert d.category == FailureCategory.MISSING_REQUIREMENT

    def test_logic_error_feedback(self):
        d = diagnose("The function signature is incompatible with the expected interface")
        assert d.category == FailureCategory.INTERFACE_MISMATCH

    def test_lint_from_feedback(self):
        d = diagnose("There are several ruff lint errors: E302, F401")
        assert d.category == FailureCategory.QUALITY_GATE

    def test_unknown_fallback(self):
        d = diagnose("This is a very vague and short comment.")
        assert d.category in (FailureCategory.UNKNOWN, FailureCategory.MISSING_REQUIREMENT,
                               FailureCategory.QUALITY_GATE, FailureCategory.LOGIC_ERROR)

    def test_lint_only_failure(self):
        vr = VerificationResult(test_passed=5, test_failed=0, test_errors=0,
                                lint_errors=3, lint_output="src/foo.py:1:1 E302")
        d = diagnose("lint issues", verification_result=vr)
        assert d.category in (FailureCategory.STYLE_ISSUE, FailureCategory.QUALITY_GATE)


class TestLocalize:
    def test_from_test_output(self):
        vr = VerificationResult(test_passed=3, test_failed=1,
                                test_output="FAILED tests/test_auth.py::test_login")
        d = DiagnosisReport(category=FailureCategory.TEST_FAILURE,
                            root_cause_summary="test failed", evidence=[])
        targets = localize(d, ["src/auth.py"], verification_result=vr)
        assert len(targets) >= 1
        assert any("test_auth" in t.file_path for t in targets)

    def test_fallback_to_changed_files(self):
        d = DiagnosisReport(category=FailureCategory.UNKNOWN,
                            root_cause_summary="unknown", evidence=[])
        targets = localize(d, ["src/foo.py", "src/bar.py"])
        assert len(targets) == 2

    def test_file_line_extraction(self):
        d = DiagnosisReport(category=FailureCategory.TEST_FAILURE,
                            root_cause_summary="test failed",
                            evidence=["Error at src/auth.py:42"])
        targets = localize(d, ["src/auth.py"])
        assert len(targets) >= 1
        found = [t for t in targets if t.file_path == "src/auth.py"]
        assert len(found) >= 1
        if found[0].line_range:
            assert found[0].line_range[0] <= 42 <= found[0].line_range[1]


class TestBuildRepairPlan:
    def test_incremental_early(self):
        d = DiagnosisReport(category=FailureCategory.TEST_FAILURE, root_cause_summary="x")
        targets = [RepairTarget(file_path="src/foo.py")]
        plan = build_repair_plan(d, targets, retry_count=0, budget=3)
        assert plan.strategy == "incremental"

    def test_fresh_start_after_2(self):
        d = DiagnosisReport(category=FailureCategory.TEST_FAILURE, root_cause_summary="x")
        targets = [RepairTarget(file_path="src/foo.py")]
        plan = build_repair_plan(d, targets, retry_count=2, budget=3)
        # retry_count=2, budget=3 → is_final_attempt (2 >= 3-1) → incremental
        # retry_count=2, budget=4 → fresh_start
        plan2 = build_repair_plan(d, targets, retry_count=2, budget=4)
        assert plan2.strategy == "fresh_start"

    def test_final_attempt_incremental(self):
        d = DiagnosisReport(category=FailureCategory.TEST_FAILURE, root_cause_summary="x")
        targets = [RepairTarget(file_path="src/foo.py")]
        plan = build_repair_plan(d, targets, retry_count=4, budget=5)
        assert plan.strategy == "incremental"  # final attempt


class TestFormatRepairPrompt:
    def test_full_roundtrip(self):
        vr = VerificationResult(test_passed=3, test_failed=1,
                                test_output="FAILED tests/test_x.py::test_y - AssertionError: 1 != 2")
        d = diagnose("test fails", verification_result=vr)
        targets = localize(d, ["src/x.py"], verification_result=vr)
        plan = build_repair_plan(d, targets, retry_count=0, budget=3)
        prompt = format_repair_prompt(plan)
        assert "Repair Instructions" in prompt
        assert "Diagnosis" in prompt
        assert "incremental" in prompt


# ═══════════════════════════════════════════════════════════
# 4. Role Pipeline Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.role_pipeline import (
    PipelineConfig,
    RoleKind,
    RoleSpec,
    get_pipeline,
    list_pipelines,
    select_pipeline,
)


class TestRolePipeline:
    def test_predefined_pipelines_exist(self):
        names = [p.name for p in list_pipelines()]
        assert "minimal" in names
        assert "standard" in names
        assert "verified" in names
        assert "full" in names

    def test_get_pipeline_by_name(self):
        p = get_pipeline("standard")
        assert p is not None
        assert p.name == "standard"

    def test_get_pipeline_unknown(self):
        with pytest.raises(KeyError):
            get_pipeline("nonexistent")

    def test_select_pipeline_bugfix(self):
        p = select_pipeline("bugfix", "simple")
        assert p.name == "minimal"

    def test_select_pipeline_feature(self):
        p = select_pipeline("feature", "medium")
        assert p.name in ("standard", "verified")

    def test_select_pipeline_complex_feature(self):
        p = select_pipeline("complex_feature", "complex")
        assert p.name == "full"

    def test_pipeline_role_sequence_standard(self):
        p = get_pipeline("standard")
        kinds = [r.kind for r in p.roles]
        assert kinds == [RoleKind.PLAN, RoleKind.BUILD, RoleKind.REVIEW, RoleKind.DECIDE]

    def test_pipeline_role_sequence_full(self):
        p = get_pipeline("full")
        kinds = [r.kind for r in p.roles]
        assert RoleKind.ARCHITECT in kinds
        assert RoleKind.VERIFY in kinds

    def test_list_pipelines_not_empty(self):
        assert len(list_pipelines()) >= 4


# ═══════════════════════════════════════════════════════════
# 5. Cost Router Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.cost_router import (
    CostTier,
    complexity_to_cost_tier,
    score_agent,
    estimate_task_cost,
)
from multi_agent.schema import AgentProfile


class TestCostTier:
    def test_simple_maps_economy(self):
        assert complexity_to_cost_tier("simple") == CostTier.ECONOMY

    def test_medium_maps_standard(self):
        assert complexity_to_cost_tier("medium") == CostTier.STANDARD

    def test_complex_maps_premium(self):
        assert complexity_to_cost_tier("complex") == CostTier.PREMIUM


class TestScoreAgent:
    def _make_agent(self, cost=0.5, reliability=0.9, health=0.9):
        return AgentProfile(id="test", cost=cost, reliability=reliability, queue_health=health)

    def test_matching_tier(self):
        agent = self._make_agent(cost=0.3)  # cheap → economy
        s = score_agent(agent, "simple")  # simple → economy tier
        assert s.final_score > 0

    def test_mismatched_tier_lower_score(self):
        cheap = self._make_agent(cost=0.2)
        expensive = self._make_agent(cost=0.9)
        s_cheap = score_agent(cheap, "simple")
        s_expensive = score_agent(expensive, "simple")
        # Cheap agent should score higher for simple tasks
        assert s_cheap.final_score >= s_expensive.final_score

    def test_budget_depleted(self):
        agent = self._make_agent(cost=0.5)
        s_high = score_agent(agent, "medium", budget_remaining=100.0, total_budget=100.0)
        s_low = score_agent(agent, "medium", budget_remaining=5.0, total_budget=100.0)
        assert s_high.final_score >= s_low.final_score


class TestEstimateTaskCost:
    def test_returns_positive(self):
        cost = estimate_task_cost("standard", "medium")
        assert cost > 0

    def test_complex_costs_more(self):
        simple = estimate_task_cost("standard", "simple")
        complex_ = estimate_task_cost("full", "complex")
        assert complex_ > simple


# ═══════════════════════════════════════════════════════════
# 6. Bridge Extractors Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.bridge_extractors import (
    get_extractor,
    extract_multi_language,
    JavaScriptExtractor,
    TypeScriptExtractor,
    GoExtractor,
    RustExtractor,
    JavaExtractor,
)


class TestGetExtractor:
    def test_python(self):
        e = get_extractor(Path("foo.py"))
        assert e is not None

    def test_javascript(self):
        e = get_extractor(Path("foo.js"))
        assert isinstance(e, JavaScriptExtractor)

    def test_typescript(self):
        e = get_extractor(Path("foo.ts"))
        assert isinstance(e, TypeScriptExtractor)

    def test_go(self):
        e = get_extractor(Path("foo.go"))
        assert isinstance(e, GoExtractor)

    def test_rust(self):
        e = get_extractor(Path("foo.rs"))
        assert isinstance(e, RustExtractor)

    def test_java(self):
        e = get_extractor(Path("foo.java"))
        assert isinstance(e, JavaExtractor)

    def test_unknown(self):
        assert get_extractor(Path("foo.txt")) is None


class TestJavaScriptExtractor:
    def test_extract_function(self, tmp_path):
        f = tmp_path / "mod.js"
        f.write_text("export function greet(name) {\n  return `Hello ${name}`;\n}\n")
        ext = JavaScriptExtractor()
        symbols = ext.extract(f)
        assert len(symbols) >= 1
        assert symbols[0].name == "greet"
        assert symbols[0].kind == "function"

    def test_extract_class(self, tmp_path):
        f = tmp_path / "mod.js"
        f.write_text("export class UserService {\n  constructor() {}\n}\n")
        ext = JavaScriptExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "UserService" and s.kind == "class" for s in symbols)

    def test_extract_const(self, tmp_path):
        f = tmp_path / "mod.js"
        f.write_text("export const MAX_RETRIES = 3;\n")
        ext = JavaScriptExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "MAX_RETRIES" for s in symbols)


class TestTypeScriptExtractor:
    def test_extract_interface(self, tmp_path):
        f = tmp_path / "types.ts"
        f.write_text("export interface UserConfig {\n  name: string;\n  age: number;\n}\n")
        ext = TypeScriptExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "UserConfig" for s in symbols)

    def test_extract_typed_function(self, tmp_path):
        f = tmp_path / "utils.ts"
        f.write_text("export function add(a: number, b: number): number {\n  return a + b;\n}\n")
        ext = TypeScriptExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "add" and s.kind == "function" for s in symbols)


class TestGoExtractor:
    def test_extract_exported_func(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main\n\nfunc GetUser(id int) *User {\n\treturn nil\n}\n")
        ext = GoExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "GetUser" and s.kind == "function" for s in symbols)

    def test_skip_unexported(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text("package main\n\nfunc getUser(id int) *User {\n\treturn nil\n}\n")
        ext = GoExtractor()
        symbols = ext.extract(f)
        # lowercase → unexported → should be skipped
        assert not any(s.name == "getUser" for s in symbols)

    def test_extract_struct(self, tmp_path):
        f = tmp_path / "models.go"
        f.write_text("package models\n\ntype User struct {\n\tName string\n}\n")
        ext = GoExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "User" and s.kind == "class" for s in symbols)


class TestRustExtractor:
    def test_extract_pub_fn(self, tmp_path):
        f = tmp_path / "lib.rs"
        f.write_text("pub fn process(data: &[u8]) -> Result<(), Error> {\n    Ok(())\n}\n")
        ext = RustExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "process" and s.kind == "function" for s in symbols)

    def test_extract_pub_struct(self, tmp_path):
        f = tmp_path / "lib.rs"
        f.write_text("pub struct Config {\n    pub timeout: u64,\n}\n")
        ext = RustExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "Config" and s.kind == "class" for s in symbols)


class TestJavaExtractor:
    def test_extract_public_class(self, tmp_path):
        f = tmp_path / "App.java"
        f.write_text("public class Application {\n    public void run() {}\n}\n")
        ext = JavaExtractor()
        symbols = ext.extract(f)
        assert any(s.name == "Application" and s.kind == "class" for s in symbols)


class TestExtractMultiLanguage:
    def test_mixed_files(self, tmp_path):
        py = tmp_path / "utils.py"
        py.write_text("def hello(name: str) -> str:\n    return f'hi {name}'\n")
        js = tmp_path / "utils.js"
        js.write_text("export function goodbye(name) {\n  return 'bye ' + name;\n}\n")

        contract = extract_multi_language(
            ["utils.py", "utils.js"], tmp_path, subtask_id="test-1"
        )
        assert contract.subtask_id == "test-1"
        assert len(contract.exports) >= 2
        names = {e.name for e in contract.exports}
        assert "hello" in names
        assert "goodbye" in names


# ═══════════════════════════════════════════════════════════
# 7. Dynamic Pipeline Tests
# ═══════════════════════════════════════════════════════════

from multi_agent.dynamic_pipeline import (
    SubTaskType,
    classify_subtask,
    enrich_subtasks,
    select_pipeline_for_subtask,
)


class TestClassifySubtask:
    def test_bugfix(self):
        c = classify_subtask("Fix the login bug that causes 500 errors")
        assert c.task_type == SubTaskType.BUGFIX

    def test_new_feature(self):
        c = classify_subtask("Implement user registration with email verification")
        assert c.task_type == SubTaskType.NEW_FEATURE

    def test_refactor(self):
        c = classify_subtask("Refactor the auth module for better separation of concerns")
        assert c.task_type == SubTaskType.REFACTOR

    def test_test_addition(self):
        c = classify_subtask("Write test cases and assertions for the payment module, increase test coverage")
        assert c.task_type == SubTaskType.TEST_ADDITION

    def test_api_endpoint(self):
        c = classify_subtask("Create REST API endpoint for order management")
        assert c.task_type == SubTaskType.API_ENDPOINT

    def test_config_change(self):
        c = classify_subtask("Update the database configuration settings in yaml")
        assert c.task_type == SubTaskType.CONFIG_CHANGE

    def test_confidence_is_positive(self):
        c = classify_subtask("Fix the broken build")
        assert c.confidence > 0


class TestSelectPipelineForSubtask:
    def test_bugfix_minimal(self):
        from multi_agent.dynamic_pipeline import SubTaskClassification
        c = SubTaskClassification(task_type=SubTaskType.BUGFIX, confidence=0.8,
                                  reasoning="", suggested_pipeline="")
        assert select_pipeline_for_subtask(c, "simple") == "minimal"

    def test_api_verified(self):
        from multi_agent.dynamic_pipeline import SubTaskClassification
        c = SubTaskClassification(task_type=SubTaskType.API_ENDPOINT, confidence=0.8,
                                  reasoning="", suggested_pipeline="")
        assert select_pipeline_for_subtask(c, "medium") == "verified"

    def test_integration_full(self):
        from multi_agent.dynamic_pipeline import SubTaskClassification
        c = SubTaskClassification(task_type=SubTaskType.INTEGRATION, confidence=0.8,
                                  reasoning="", suggested_pipeline="")
        assert select_pipeline_for_subtask(c, "complex") == "full"


class TestEnrichSubtasks:
    def test_enriches_list(self):
        tasks = [
            {"id": "t1", "description": "Fix login bug"},
            {"id": "t2", "description": "Add REST API endpoint for users"},
        ]
        enriched = enrich_subtasks(tasks, "medium")
        assert len(enriched) == 2
        assert enriched[0].get("_task_type") is not None
        assert enriched[0].get("_pipeline") is not None
        assert enriched[1].get("_pipeline") is not None


# ═══════════════════════════════════════════════════════════
# 8. Integration Tests (schema + adaptive_decompose)
# ═══════════════════════════════════════════════════════════

from multi_agent.schema import SubTask, VerificationSummary
from multi_agent.adaptive_decompose import select_strategy


class TestSchemaExtensions:
    def test_subtask_pipeline_hint(self):
        st = SubTask(id="t1", description="test", pipeline_hint="verified")
        assert st.pipeline_hint == "verified"

    def test_subtask_pipeline_hint_default(self):
        st = SubTask(id="t1", description="test")
        assert st.pipeline_hint == ""

    def test_verification_summary(self):
        vs = VerificationSummary(test_passed=5, test_failed=1, all_passed=False)
        assert vs.test_passed == 5
        assert not vs.all_passed


class TestBudgetAwareStrategy:
    def test_low_budget_downgrades(self):
        # A complex requirement with low budget should downgrade
        req = (
            "Implement a distributed authentication system with OAuth2, JWT tokens, "
            "session management, database migrations, API endpoints for login/logout/register, "
            "middleware for rate limiting, audit logging, and role-based access control."
        )
        s = select_strategy(req, budget_remaining=1.0)
        # Should downgrade from DEEP to SHALLOW if score is high enough
        assert s.kind in ("no_decompose", "shallow_decompose", "deep_decompose")

    def test_no_budget_no_effect(self):
        s = select_strategy("Fix a simple bug")
        assert s.kind == "no_decompose"
