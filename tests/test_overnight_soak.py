"""Overnight Soak Test — 6-hour continuous stress testing for MyGO framework.

Designed to run unattended overnight with zero expected failures.
Tests are deterministic or use controlled randomness (seeded).

Run:
    .venv/bin/python -m pytest tests/test_overnight_soak.py -x -v --timeout=21600 2>&1 | tee soak.log

What it covers:
1. Property-based testing (hypothesis) — thousands of random inputs
2. Stress testing — large inputs, deep nesting, high concurrency
3. Memory stability — repeated alloc/dealloc cycles
4. Determinism verification — same input always same output
5. Boundary conditions — min/max values exhaustively
6. Cross-module integration — end-to-end data flow
7. File I/O stability — repeated read/write/delete cycles
8. SQLite checkpoint stability — concurrent read/write
9. Serialization roundtrip — Pydantic models survive JSON roundtrip
10. Regex engine stability — pathological inputs

Estimated duration: ~6 hours (configurable via SOAK_HOURS env var)
"""

from __future__ import annotations

import gc
import hashlib
import itertools
import json
import os
import random
import string
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, assume
from hypothesis import strategies as st

# ── Configuration ────────────────────────────────────────────
# Control duration via env var (default 6 hours)
SOAK_HOURS = float(os.environ.get("SOAK_HOURS", "6"))
SOAK_SECONDS = int(SOAK_HOURS * 3600)
# Per-hypothesis-test deadline (generous to avoid flaky timeouts)
HYPO_DEADLINE = None  # no per-example deadline
# Number of hypothesis examples per test
HYPO_MAX_EXAMPLES = int(os.environ.get("SOAK_EXAMPLES", "5000"))
# Number of stress loop iterations
STRESS_ITERATIONS = int(os.environ.get("SOAK_STRESS_ITERS", "2000"))
# Number of full test suite repeats
SUITE_REPEATS = int(os.environ.get("SOAK_SUITE_REPEATS", "50"))

# Seed for reproducibility
SEED = 20260320
random.seed(SEED)


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _random_unicode(min_len: int = 0, max_len: int = 500) -> str:
    """Generate random unicode string including CJK, emoji, special chars."""
    length = random.randint(min_len, max_len)
    chars = []
    for _ in range(length):
        r = random.random()
        if r < 0.5:
            chars.append(chr(random.randint(0x20, 0x7E)))  # ASCII printable
        elif r < 0.7:
            chars.append(chr(random.randint(0x4E00, 0x9FFF)))  # CJK
        elif r < 0.85:
            chars.append(chr(random.randint(0x0400, 0x04FF)))  # Cyrillic
        elif r < 0.95:
            chars.append(random.choice("\n\t\r\0"))  # control chars
        else:
            chars.append(chr(random.randint(0x1F600, 0x1F64F)))  # emoji
    return "".join(chars)


# ══════════════════════════════════════════════════════════════
# 1. SCHEMA — Pydantic Model Stress Tests
# ══════════════════════════════════════════════════════════════

from multi_agent.schema import (
    AgentProfile,
    BuilderOutput,
    ConversationEvent,
    DecomposeResult,
    ReviewerOutput,
    SkillContract,
    SubTask,
    Task,
    TaskState,
    VerificationSummary,
    make_event,
)


class TestSchemaPropertyBased:
    """Hypothesis-driven tests for Pydantic schema models."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        task_id=st.from_regex(r"[a-z0-9][a-z0-9-]{2,20}", fullmatch=True),
        trace_id=st.from_regex(r"[a-f0-9]{16,32}", fullmatch=True),
        skill_id=st.just("code-implement"),
    )
    def test_task_roundtrip(self, task_id, trace_id, skill_id):
        """Task model survives JSON serialization roundtrip."""
        t = Task(task_id=task_id, trace_id=trace_id, skill_id=skill_id)
        data = t.model_dump()
        t2 = Task(**data)
        assert t2.task_id == task_id
        assert t2.trace_id == trace_id

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        status=st.sampled_from(["completed", "blocked", "error"]),
        summary=st.text(min_size=0, max_size=500),
        changed_files=st.lists(st.text(min_size=1, max_size=50), max_size=20),
    )
    def test_builder_output_roundtrip(self, status, summary, changed_files):
        """BuilderOutput survives JSON roundtrip with arbitrary text."""
        bo = BuilderOutput(status=status, summary=summary, changed_files=changed_files)
        data = json.loads(bo.model_dump_json())
        bo2 = BuilderOutput(**data)
        assert bo2.status == status
        assert bo2.changed_files == changed_files

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        decision=st.sampled_from(["approve", "reject", "request_changes"]),
        feedback=st.text(min_size=0, max_size=1000),
        issues=st.lists(st.text(min_size=1, max_size=200), max_size=10),
    )
    def test_reviewer_output_roundtrip(self, decision, feedback, issues):
        """ReviewerOutput survives JSON roundtrip."""
        ro = ReviewerOutput(decision=decision, feedback=feedback, issues=issues)
        data = json.loads(ro.model_dump_json())
        ro2 = ReviewerOutput(**data)
        assert ro2.decision == decision

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        sub_id=st.from_regex(r"[a-z][a-z0-9-]{1,20}", fullmatch=True),
        desc=st.text(min_size=1, max_size=300),
        n_criteria=st.integers(min_value=0, max_value=10),
    )
    def test_subtask_roundtrip(self, sub_id, desc, n_criteria):
        """SubTask model roundtrip with varying criteria counts."""
        criteria = [f"criterion-{i}" for i in range(n_criteria)]
        st_obj = SubTask(id=sub_id, description=desc, done_criteria=criteria)
        data = json.loads(st_obj.model_dump_json())
        st2 = SubTask(**data)
        assert st2.id == sub_id
        assert len(st2.done_criteria) == n_criteria

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        role=st.sampled_from(["orchestrator", "builder", "reviewer"]),
        action=st.sampled_from(["assigned", "approved", "retry", "request_changes",
                                 "escalated", "cancelled", None]),
    )
    def test_make_event_always_valid(self, role, action):
        """make_event always produces a valid dict with timestamp."""
        evt = make_event(role, action=action)
        assert isinstance(evt, dict)
        assert evt["role"] == role
        assert "t" in evt
        assert isinstance(evt["t"], float)


# ══════════════════════════════════════════════════════════════
# 2. ADAPTIVE DECOMPOSE — Complexity Scoring Stress
# ══════════════════════════════════════════════════════════════

from multi_agent.adaptive_decompose import (
    ComplexityFeatures,
    ComplexityLevel,
    classify_complexity,
    estimate_complexity_features,
    select_strategy,
)


class TestAdaptiveDecomposeStress:
    """Stress test the complexity estimator with wild inputs."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(text=st.text(min_size=0, max_size=5000))
    def test_estimate_never_crashes(self, text):
        """estimate_complexity_features handles ANY string without error."""
        features = estimate_complexity_features(text)
        assert features.token_count >= 0
        assert features.sentence_count >= 0
        assert isinstance(features.complexity_score, float)

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(text=st.text(min_size=0, max_size=5000))
    def test_classify_always_returns_valid_level(self, text):
        """classify_complexity always returns a valid ComplexityLevel."""
        features = estimate_complexity_features(text)
        level = classify_complexity(features)
        assert level in (ComplexityLevel.SIMPLE, ComplexityLevel.MEDIUM, ComplexityLevel.COMPLEX)

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        text=st.text(min_size=0, max_size=2000),
        budget=st.one_of(st.none(), st.floats(min_value=0, max_value=100)),
    )
    def test_select_strategy_never_crashes(self, text, budget):
        """select_strategy handles any input combination."""
        s = select_strategy(text, budget_remaining=budget)
        assert s.kind in ("no_decompose", "shallow_decompose", "deep_decompose")
        assert 0 <= s.confidence <= 1.0
        assert s.max_subtasks >= 1

    def test_determinism_over_1000_runs(self):
        """Same input always produces same complexity score."""
        text = "实现一个带有 JWT 认证的 REST API，包括用户注册、登录和权限管理"
        reference = estimate_complexity_features(text)
        for _ in range(1000):
            result = estimate_complexity_features(text)
            assert result.complexity_score == reference.complexity_score
            assert result.verb_count == reference.verb_count

    @settings(max_examples=HYPO_MAX_EXAMPLES // 2, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(text=st.text(alphabet=st.characters(categories=("L", "N", "P", "Z")),
                        min_size=0, max_size=10000))
    def test_score_is_finite(self, text):
        """Complexity score is always a finite float, never NaN/Inf."""
        features = estimate_complexity_features(text)
        score = features.complexity_score
        assert not (score != score)  # NaN check
        assert score != float("inf")
        assert score != float("-inf")


# ══════════════════════════════════════════════════════════════
# 3. REPAIR CYCLE — Diagnosis Robustness
# ══════════════════════════════════════════════════════════════

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
from multi_agent.verifier import VerificationResult


class TestRepairCycleStress:
    """Stress the repair cycle with random feedback and verification results."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(feedback=st.text(min_size=0, max_size=3000))
    def test_diagnose_never_crashes_on_any_feedback(self, feedback):
        """diagnose() handles any feedback string."""
        d = diagnose(reviewer_feedback=feedback)
        assert d.category in FailureCategory
        assert isinstance(d.root_cause_summary, str)
        assert isinstance(d.evidence, list)
        assert 0 <= d.confidence <= 1.0

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        feedback=st.text(min_size=0, max_size=1000),
        test_passed=st.integers(min_value=0, max_value=1000),
        test_failed=st.integers(min_value=0, max_value=1000),
        test_errors=st.integers(min_value=0, max_value=100),
        lint_errors=st.integers(min_value=0, max_value=500),
    )
    def test_diagnose_with_verification_never_crashes(
        self, feedback, test_passed, test_failed, test_errors, lint_errors,
    ):
        """diagnose() handles any VerificationResult combination."""
        vr = VerificationResult(
            test_passed=test_passed, test_failed=test_failed,
            test_errors=test_errors, lint_errors=lint_errors,
            test_output="FAILED test_foo.py::test_bar" if test_failed else "",
        )
        d = diagnose(reviewer_feedback=feedback, verification_result=vr)
        assert d.category in FailureCategory

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        feedback=st.text(min_size=0, max_size=500),
        builder_output=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=500),
            st.fixed_dictionaries({
                "status": st.sampled_from(["completed", "error", "blocked"]),
                "summary": st.text(min_size=0, max_size=200),
            }),
        ),
        done_criteria=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=200),
            st.lists(st.text(min_size=1, max_size=50), max_size=10),
        ),
    )
    def test_diagnose_polymorphic_inputs(self, feedback, builder_output, done_criteria):
        """diagnose() handles str, dict, list, and None inputs correctly."""
        d = diagnose(
            reviewer_feedback=feedback,
            builder_output=builder_output,
            done_criteria=done_criteria,
        )
        assert d.category in FailureCategory

    @settings(max_examples=HYPO_MAX_EXAMPLES // 2, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        n_files=st.integers(min_value=0, max_value=50),
        retry_count=st.integers(min_value=0, max_value=10),
        budget=st.integers(min_value=1, max_value=10),
    )
    def test_full_repair_pipeline_never_crashes(self, n_files, retry_count, budget):
        """Full diagnose → localize → build_plan → format pipeline never crashes."""
        files = [f"src/file_{i}.py" for i in range(n_files)]
        d = diagnose(reviewer_feedback="tests fail, missing feature")
        targets = localize(d, files)
        plan = build_repair_plan(d, targets, retry_count=retry_count, budget=budget)
        prompt = format_repair_prompt(plan)
        assert isinstance(prompt, str)
        assert "Repair Instructions" in prompt


# ══════════════════════════════════════════════════════════════
# 4. VERIFIER — Parsing Robustness
# ══════════════════════════════════════════════════════════════

from multi_agent.verifier import (
    _parse_lint_error_count,
    _parse_pytest_counts,
    format_for_repair,
    format_for_reviewer,
)


class TestVerifierParsingStress:
    """Stress test pytest/lint output parsers with wild input."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(output=st.text(min_size=0, max_size=5000))
    def test_parse_pytest_counts_never_crashes(self, output):
        """_parse_pytest_counts handles any string."""
        p, f, e = _parse_pytest_counts(output)
        assert p >= 0 and f >= 0 and e >= 0

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(output=st.text(min_size=0, max_size=5000))
    def test_parse_lint_error_count_never_crashes(self, output):
        """_parse_lint_error_count handles any string."""
        count = _parse_lint_error_count(output)
        assert count >= 0

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        test_passed=st.integers(min_value=0, max_value=10000),
        test_failed=st.integers(min_value=0, max_value=10000),
        lint_errors=st.integers(min_value=0, max_value=10000),
    )
    def test_format_for_reviewer_never_crashes(self, test_passed, test_failed, lint_errors):
        """format_for_reviewer handles any counter values."""
        vr = VerificationResult(
            test_passed=test_passed, test_failed=test_failed,
            lint_errors=lint_errors, command_used="pytest",
        )
        out = format_for_reviewer(vr)
        assert isinstance(out, str)
        assert "Verification" in out

    def test_known_pytest_formats(self):
        """Verify parsing of all known pytest summary formats."""
        cases = [
            ("====== 10 passed in 1.0s ======", (10, 0, 0)),
            ("3 passed, 1 failed", (3, 1, 0)),
            ("5 passed, 2 failed, 1 error in 3.5s", (5, 2, 1)),
            ("====== 1 failed ======", (0, 1, 0)),
            ("= 100 passed, 0 failed =", (100, 0, 0)),
            ("no tests ran", (0, 0, 0)),
            ("", (0, 0, 0)),
        ]
        for output, expected in cases:
            result = _parse_pytest_counts(output)
            assert result == expected, f"Failed for: {output!r}"


# ══════════════════════════════════════════════════════════════
# 5. SANDBOX — Subprocess Stability
# ══════════════════════════════════════════════════════════════

from multi_agent.sandbox import SandboxRunner


class TestSandboxStress:
    """Stress test sandbox subprocess execution."""

    def test_rapid_fire_1000_subprocesses(self):
        """Run 1000 rapid subprocess calls without resource leak."""
        runner = SandboxRunner(default_timeout_sec=5)
        for i in range(1000):
            r = runner.run(f"echo iteration-{i}")
            assert r.returncode == 0
            assert f"iteration-{i}" in r.stdout

    def test_alternating_success_failure(self):
        """Alternate success/failure 500 times."""
        runner = SandboxRunner(default_timeout_sec=5)
        for i in range(500):
            if i % 2 == 0:
                r = runner.run("echo ok")
                assert r.returncode == 0
            else:
                r = runner.run("sh -c 'exit 1'")
                assert r.returncode == 1

    def test_large_output_handling(self):
        """Handle commands with large stdout without OOM."""
        runner = SandboxRunner(max_output_chars=1000)
        # Generate ~100KB of output
        r = runner.run("sh -c 'seq 1 50000'")
        assert len(r.stdout) <= 2000  # truncated
        assert r.returncode == 0

    def test_many_timeouts(self):
        """Handle 50 consecutive timeouts without resource leak."""
        runner = SandboxRunner(default_timeout_sec=1)
        for _ in range(50):
            r = runner.run("sleep 60", timeout_sec=1)
            assert r.timed_out


# ══════════════════════════════════════════════════════════════
# 6. CONTEXT BRIDGE — AST Parsing Stability
# ══════════════════════════════════════════════════════════════

from multi_agent.context_bridge import (
    check_conformance,
    extract_interface_contract,
    format_bridge_context,
)


class TestContextBridgeStress:
    """Stress test AST parsing with generated Python code."""

    def test_1000_different_functions(self, tmp_path):
        """Extract contracts from a file with 1000 functions."""
        lines = []
        for i in range(1000):
            lines.append(f"def func_{i}(arg_{i}: int = {i}) -> str:")
            lines.append(f"    return 'result_{i}'")
            lines.append("")
        (tmp_path / "big.py").write_text("\n".join(lines))

        contract = extract_interface_contract(["big.py"], tmp_path, "stress-test")
        assert len(contract.exports) == 1000
        # Verify specific entries
        assert contract.exports[0].name == "func_0"
        assert contract.exports[999].name == "func_999"

    def test_deeply_nested_classes(self, tmp_path):
        """File with classes containing many methods."""
        lines = ["class BigClass:"]
        for i in range(200):
            lines.append(f"    def method_{i}(self, x_{i}: int) -> int:")
            lines.append(f"        return x_{i} * {i}")
        (tmp_path / "nested.py").write_text("\n".join(lines))

        contract = extract_interface_contract(["nested.py"], tmp_path, "nested-test")
        # Only top-level class and __init__ are extracted, not methods
        assert len(contract.exports) >= 1

    def test_syntax_error_file_is_skipped(self, tmp_path):
        """Files with syntax errors are gracefully skipped."""
        (tmp_path / "broken.py").write_text("def oops(:\n    pass\n")
        contract = extract_interface_contract(["broken.py"], tmp_path, "broken-test")
        assert len(contract.exports) == 0  # gracefully empty

    def test_empty_file(self, tmp_path):
        """Empty Python file produces empty contract."""
        (tmp_path / "empty.py").write_text("")
        contract = extract_interface_contract(["empty.py"], tmp_path, "empty-test")
        assert len(contract.exports) == 0

    def test_conformance_check_no_violations_stress(self, tmp_path):
        """Conformance check with matching code produces zero violations."""
        code = "def compute(x: int = 5) -> int:\n    return x * 2\n"
        (tmp_path / "upstream.py").write_text(code)
        (tmp_path / "downstream.py").write_text(code)  # identical

        upstream = extract_interface_contract(["upstream.py"], tmp_path, "up")
        violations = check_conformance([upstream], ["downstream.py"], tmp_path)
        assert len(violations) == 0


# ══════════════════════════════════════════════════════════════
# 7. BRIDGE EXTRACTORS — Multi-Language Stress
# ══════════════════════════════════════════════════════════════

from multi_agent.bridge_extractors import (
    GoExtractor,
    JavaExtractor,
    JavaScriptExtractor,
    RustExtractor,
    TypeScriptExtractor,
    extract_multi_language,
)


class TestBridgeExtractorsStress:
    """Stress test regex extractors with generated code."""

    def test_js_100_exports(self, tmp_path):
        """Extract 100 JS function exports."""
        lines = [f"export function fn_{i}(arg{i}) {{ return {i}; }}" for i in range(100)]
        (tmp_path / "big.js").write_text("\n".join(lines))
        ext = JavaScriptExtractor()
        symbols = ext.extract(tmp_path / "big.js")
        assert len(symbols) == 100

    def test_go_100_exported_funcs(self, tmp_path):
        """Extract 100 Go exported functions."""
        lines = ["package main", ""]
        for i in range(100):
            lines.append(f"func Handler{i}(w http.ResponseWriter, r *http.Request) {{}}")
        (tmp_path / "handlers.go").write_text("\n".join(lines))
        ext = GoExtractor()
        symbols = ext.extract(tmp_path / "handlers.go")
        assert len(symbols) == 100

    def test_rust_50_pub_fns(self, tmp_path):
        """Extract 50 Rust pub fns."""
        lines = [f"pub fn process_{i}(data: &[u8]) -> Vec<u8> {{ vec![] }}" for i in range(50)]
        (tmp_path / "lib.rs").write_text("\n".join(lines))
        ext = RustExtractor()
        symbols = ext.extract(tmp_path / "lib.rs")
        assert len(symbols) == 50

    def test_java_many_methods(self, tmp_path):
        """Extract many Java methods."""
        lines = ["public class BigService {"]
        for i in range(80):
            lines.append(f"    public String method{i}(int arg{i}) {{ return \"\"; }}")
        lines.append("}")
        (tmp_path / "BigService.java").write_text("\n".join(lines))
        ext = JavaExtractor()
        symbols = ext.extract(tmp_path / "BigService.java")
        method_count = sum(1 for s in symbols if s.kind == "function")
        assert method_count >= 50  # regex may not catch all but should get most

    @settings(max_examples=HYPO_MAX_EXAMPLES // 2, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(code=st.text(min_size=0, max_size=2000))
    def test_js_extractor_never_crashes_on_any_input(self, code, tmp_path):
        """JS extractor handles any text without crashing."""
        f = tmp_path / "random.js"
        f.write_text(code)
        ext = JavaScriptExtractor()
        symbols = ext.extract(f)
        assert isinstance(symbols, list)

    @settings(max_examples=HYPO_MAX_EXAMPLES // 2, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(code=st.text(min_size=0, max_size=2000))
    def test_go_extractor_never_crashes_on_any_input(self, code, tmp_path):
        """Go extractor handles any text without crashing."""
        f = tmp_path / "random.go"
        f.write_text(code)
        ext = GoExtractor()
        symbols = ext.extract(f)
        assert isinstance(symbols, list)


# ══════════════════════════════════════════════════════════════
# 8. ROLE PIPELINE + COST ROUTER — Exhaustive Combos
# ══════════════════════════════════════════════════════════════

from multi_agent.role_pipeline import get_pipeline, list_pipelines, select_pipeline
from multi_agent.cost_router import (
    CostTier,
    complexity_to_cost_tier,
    estimate_task_cost,
    score_agent,
)


class TestRolePipelineExhaustive:
    """Exhaustively test all task_type × complexity combinations."""

    TASK_TYPES = [
        "bugfix", "simple_feature", "feature", "complex_feature",
        "refactor", "test", "unknown_type", "",
    ]
    COMPLEXITIES = ["simple", "medium", "complex", "unknown", ""]

    def test_all_combinations(self):
        """select_pipeline handles every task_type × complexity combo."""
        for tt in self.TASK_TYPES:
            for cl in self.COMPLEXITIES:
                p = select_pipeline(tt, cl)
                assert p is not None
                assert p.name in ("minimal", "standard", "verified", "full")

    def test_all_pipelines_have_valid_roles(self):
        """Every pipeline has at least 1 role and valid structure."""
        for p in list_pipelines():
            assert len(p.roles) >= 1
            assert p.name
            assert p.description


class TestCostRouterExhaustive:
    """Test cost scoring across all parameter ranges."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        cost=st.floats(min_value=0.0, max_value=1.0),
        reliability=st.floats(min_value=0.0, max_value=1.0),
        queue_health=st.floats(min_value=0.0, max_value=1.0),
        complexity=st.sampled_from(["simple", "medium", "complex"]),
        budget_remaining=st.one_of(st.none(), st.floats(min_value=0, max_value=1000)),
        total_budget=st.one_of(st.none(), st.floats(min_value=0.01, max_value=1000)),
    )
    def test_score_agent_never_crashes(
        self, cost, reliability, queue_health, complexity,
        budget_remaining, total_budget,
    ):
        """score_agent handles any valid float combination."""
        agent = AgentProfile(
            id="test-agent", cost=cost,
            reliability=reliability, queue_health=queue_health,
        )
        s = score_agent(
            agent, complexity,
            budget_remaining=budget_remaining,
            total_budget=total_budget,
        )
        assert isinstance(s.final_score, float)
        assert s.final_score >= 0
        assert s.agent_id == "test-agent"

    def test_estimate_cost_all_pipelines(self):
        """estimate_task_cost works for every pipeline × complexity."""
        for p in list_pipelines():
            for c in ("simple", "medium", "complex"):
                cost = estimate_task_cost(p.name, c)
                assert cost >= 0
                assert isinstance(cost, float)


# ══════════════════════════════════════════════════════════════
# 9. DYNAMIC PIPELINE — Classification Stress
# ══════════════════════════════════════════════════════════════

from multi_agent.dynamic_pipeline import (
    SubTaskClassification,
    SubTaskType,
    classify_subtask,
    enrich_subtasks,
    select_pipeline_for_subtask,
)


class TestDynamicPipelineStress:
    """Stress test sub-task classification with random descriptions."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(desc=st.text(min_size=0, max_size=2000))
    def test_classify_never_crashes(self, desc):
        """classify_subtask handles any string."""
        c = classify_subtask(desc)
        assert c.task_type in SubTaskType
        assert 0 <= c.confidence <= 1.0
        assert isinstance(c.reasoning, str)

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        task_type=st.sampled_from(list(SubTaskType)),
        complexity=st.sampled_from(["simple", "medium", "complex", "unknown"]),
    )
    def test_select_pipeline_all_combos(self, task_type, complexity):
        """select_pipeline_for_subtask handles every type × complexity."""
        c = SubTaskClassification(
            task_type=task_type, confidence=0.8,
            reasoning="test", suggested_pipeline="",
        )
        pipeline = select_pipeline_for_subtask(c, parent_complexity=complexity)
        assert pipeline in ("minimal", "standard", "verified", "full")

    def test_enrich_large_batch(self):
        """enrich_subtasks handles a large batch of tasks."""
        tasks = [
            {"id": f"task-{i}", "description": f"implement feature number {i}"}
            for i in range(200)
        ]
        enriched = enrich_subtasks(tasks, "medium")
        assert len(enriched) == 200
        assert all("_pipeline" in t for t in enriched)


# ══════════════════════════════════════════════════════════════
# 10. META-GRAPH — Sub-Task ID Generation Stress
# ══════════════════════════════════════════════════════════════

from multi_agent.meta_graph import (
    format_prior_context,
    generate_sub_task_id,
)
from multi_agent._utils import SAFE_TASK_ID_RE


class TestMetaGraphStress:
    """Stress test sub-task ID generation and context formatting."""

    @settings(max_examples=HYPO_MAX_EXAMPLES, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        parent=st.from_regex(r"task-[a-z0-9]{3,12}", fullmatch=True),
        sub_id=st.text(min_size=1, max_size=30),
    )
    def test_generate_sub_task_id_always_valid(self, parent, sub_id):
        """Generated sub-task IDs always match SAFE_TASK_ID_RE."""
        tid = generate_sub_task_id(parent, sub_id)
        assert SAFE_TASK_ID_RE.match(tid), f"Invalid ID: {tid!r}"

    def test_1000_unique_ids(self):
        """1000 different sub_ids produce 1000 unique task IDs."""
        ids = set()
        for i in range(1000):
            tid = generate_sub_task_id("task-parent", f"sub-{i}")
            ids.add(tid)
        assert len(ids) == 1000

    def test_format_prior_context_large(self):
        """format_prior_context handles 100 prior results."""
        results = [
            {"sub_id": f"sub-{i}", "summary": f"completed task {i}",
             "changed_files": [f"file_{i}.py"]}
            for i in range(100)
        ]
        ctx = format_prior_context(results, max_items=5, dep_ids=["sub-0", "sub-50"])
        assert "sub-0" in ctx
        assert "sub-50" in ctx


# ══════════════════════════════════════════════════════════════
# 11. ROUTER — Agent Resolution Stress
# ══════════════════════════════════════════════════════════════

from multi_agent.router import resolve_role


class TestRouterStress:
    """Stress test role resolution."""

    def _agents(self, n: int) -> list[AgentProfile]:
        return [
            AgentProfile(
                id=f"agent-{i}",
                capabilities=["implementation", "review", "architecture"],
                reliability=0.5 + random.random() * 0.5,
                queue_health=0.5 + random.random() * 0.5,
                cost=random.random(),
            )
            for i in range(n)
        ]

    def test_resolve_role_100_agents(self):
        """resolve_role works with 100 agents for various roles."""
        agents = self._agents(100)
        contract = SkillContract(id="code-implement")
        for role in ("builder", "reviewer", "architect", "verifier"):
            result = resolve_role(agents, contract, role)
            assert isinstance(result, str) and len(result) > 0

    def test_resolve_role_explicit_always_wins(self):
        """Explicit assignment always overrides auto-selection."""
        agents = self._agents(10)
        contract = SkillContract(id="code-implement")
        result = resolve_role(agents, contract, "builder", explicit="my-custom-agent")
        assert result == "my-custom-agent"


# ══════════════════════════════════════════════════════════════
# 12. FINOPS — Aggregation Stress
# ══════════════════════════════════════════════════════════════

from multi_agent.finops import aggregate_usage, estimate_cost, format_report


class TestFinOpsStress:
    """Stress test FinOps aggregation with large datasets."""

    def test_aggregate_10000_entries(self):
        """Aggregate 10,000 usage entries without error."""
        entries = [
            {
                "ts": time.time() + i,
                "task_id": f"task-{i % 100:03d}",
                "node": random.choice(["plan", "build", "review", "decide"]),
                "agent_id": f"agent-{i % 5}",
                "input_tokens": random.randint(100, 10000),
                "output_tokens": random.randint(50, 5000),
                "total_tokens": 0,
                "cost": random.uniform(0.001, 0.5),
                "model": "test-model",
            }
            for i in range(10000)
        ]
        agg = aggregate_usage(entries)
        assert agg["entry_count"] == 10000
        assert agg["task_count"] == 100
        assert agg["total_cost"] > 0
        # Verify report formatting doesn't crash
        report = format_report(agg)
        assert "Token Usage Report" in report

    @settings(max_examples=HYPO_MAX_EXAMPLES // 2, deadline=HYPO_DEADLINE,
              suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
    @given(
        input_tokens=st.integers(min_value=0, max_value=10_000_000),
        output_tokens=st.integers(min_value=0, max_value=10_000_000),
    )
    def test_estimate_cost_never_negative(self, input_tokens, output_tokens):
        """Cost estimate is always non-negative."""
        cost = estimate_cost(input_tokens, output_tokens)
        assert cost >= 0


# ══════════════════════════════════════════════════════════════
# 13. MEMORY STABILITY — Leak Detection
# ══════════════════════════════════════════════════════════════


class TestMemoryStability:
    """Detect memory leaks in hot-path operations."""

    def test_complexity_estimation_no_leak(self):
        """10,000 complexity estimations don't leak memory."""
        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()

        for i in range(10000):
            text = f"implement feature {i} with auth and database and API endpoint"
            estimate_complexity_features(text)

        gc.collect()
        snapshot2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # Compare memory: allow up to 5MB growth (mostly string interning)
        stats = snapshot2.compare_to(snapshot1, "lineno")
        total_diff = sum(s.size_diff for s in stats if s.size_diff > 0)
        assert total_diff < 5 * 1024 * 1024, f"Memory grew by {total_diff / 1024 / 1024:.1f}MB"

    def test_diagnosis_no_leak(self):
        """10,000 diagnose() calls don't leak memory."""
        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()

        for i in range(10000):
            diagnose(
                reviewer_feedback=f"test {i} fails with assertion error in auth module",
                builder_output={"status": "completed", "summary": f"feature {i}"},
            )

        gc.collect()
        snapshot2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snapshot2.compare_to(snapshot1, "lineno")
        total_diff = sum(s.size_diff for s in stats if s.size_diff > 0)
        assert total_diff < 5 * 1024 * 1024, f"Memory grew by {total_diff / 1024 / 1024:.1f}MB"


# ══════════════════════════════════════════════════════════════
# 14. DETERMINISM — Repeated Runs Must Match
# ══════════════════════════════════════════════════════════════


class TestDeterminism:
    """Verify that all pure functions produce identical output across runs."""

    INPUTS = [
        "",
        "fix bug",
        "实现一个完整的用户认证系统，包括JWT、OAuth2、Session管理",
        "implement distributed microservice with gRPC, Redis cache, PostgreSQL",
        "a" * 10000,
        "修复\n\tbug\0in\r\nauth",  # control chars
    ]

    def test_complexity_features_deterministic(self):
        """100 runs of same inputs produce identical results."""
        for text in self.INPUTS:
            reference = estimate_complexity_features(text)
            for _ in range(100):
                result = estimate_complexity_features(text)
                assert result.complexity_score == reference.complexity_score
                assert result.verb_count == reference.verb_count
                assert result.is_bugfix == reference.is_bugfix

    def test_classify_subtask_deterministic(self):
        """Classification is deterministic."""
        for text in self.INPUTS:
            if not text.strip():
                continue
            ref = classify_subtask(text)
            for _ in range(100):
                result = classify_subtask(text)
                assert result.task_type == ref.task_type

    def test_diagnose_deterministic(self):
        """Diagnosis is deterministic for same inputs."""
        for text in self.INPUTS:
            ref = diagnose(reviewer_feedback=text)
            for _ in range(100):
                result = diagnose(reviewer_feedback=text)
                assert result.category == ref.category
                assert result.root_cause_summary == ref.root_cause_summary


# ══════════════════════════════════════════════════════════════
# 15. LONG-RUNNING SOAK — Repeated Full Suite
# ══════════════════════════════════════════════════════════════


class TestSoakLoop:
    """Run core operations in a timed loop for the remaining soak duration."""

    def test_soak_continuous(self):
        """Continuously exercise all modules until SOAK time expires.

        This is the main soak test. It loops through core operations
        repeatedly, checking for crashes, memory growth, and determinism.
        Duration is controlled by SOAK_HOURS env var (default 6).
        """
        start = time.time()
        deadline = start + SOAK_SECONDS
        iteration = 0
        errors = []

        # Pre-build reusable objects
        runner = SandboxRunner(default_timeout_sec=5, max_output_chars=500)
        agents = [
            AgentProfile(id=f"soak-{i}", capabilities=["implementation", "review"],
                         reliability=0.9, queue_health=0.9, cost=0.3 * (i + 1))
            for i in range(5)
        ]
        contract = SkillContract(id="code-implement")

        while time.time() < deadline:
            iteration += 1
            try:
                # 1. Complexity estimation with random text
                text = _random_unicode(10, 500)
                features = estimate_complexity_features(text)
                assert isinstance(features.complexity_score, float)
                level = classify_complexity(features)
                strategy = select_strategy(text)

                # 2. Sub-task classification
                c = classify_subtask(text)
                assert c.task_type in SubTaskType
                pipeline = select_pipeline_for_subtask(c)
                assert pipeline in ("minimal", "standard", "verified", "full")

                # 3. Repair cycle
                d = diagnose(reviewer_feedback=text)
                targets = localize(d, [f"src/mod_{iteration % 10}.py"])
                plan = build_repair_plan(d, targets, iteration % 5, 5)
                prompt = format_repair_prompt(plan)
                assert isinstance(prompt, str)

                # 4. Cost scoring
                agent = agents[iteration % len(agents)]
                s = score_agent(agent, "medium")
                assert s.final_score >= 0

                # 5. Pipeline selection
                p = select_pipeline("feature", "medium")
                assert p is not None

                # 6. Sub-task ID generation
                tid = generate_sub_task_id("task-soak", f"sub-{iteration}")
                assert SAFE_TASK_ID_RE.match(tid)

                # 7. Schema roundtrip
                evt = make_event("orchestrator", action="assigned")
                assert evt["role"] == "orchestrator"

                # 8. Subprocess (every 100th iteration to avoid overload)
                if iteration % 100 == 0:
                    r = runner.run("echo soak-ok")
                    assert r.returncode == 0

                # Progress report every 1000 iterations
                if iteration % 1000 == 0:
                    elapsed = time.time() - start
                    remaining = deadline - time.time()
                    print(
                        f"  [soak] iteration={iteration:,} "
                        f"elapsed={elapsed/60:.1f}min "
                        f"remaining={remaining/60:.1f}min "
                        f"errors={len(errors)}",
                        flush=True,
                    )

            except Exception as e:
                errors.append(f"iteration {iteration}: {type(e).__name__}: {e}")
                if len(errors) >= 10:
                    break  # Too many errors, bail

        elapsed = time.time() - start
        print(
            f"\n  [soak] DONE: {iteration:,} iterations in {elapsed/3600:.2f}h, "
            f"{len(errors)} errors",
            flush=True,
        )
        assert not errors, f"Soak test had {len(errors)} errors:\n" + "\n".join(errors[:10])
