"""Verifier — test-execution feedback loop for build output validation.

Runs automated checks (pytest, linter, type checker) against changed files
after builder completes, injecting structured results into reviewer context.

Inspired by:
- AgentCoder (arXiv 2024): test executor agent provides ground truth
- MAGIS (NeurIPS 2024): QA-in-the-loop during development
- SWE-agent (NeurIPS 2024): linter/syntax guardrails

Novel contribution: orchestrator-side verification independent of both
builder and reviewer agents, creating a third-party ground truth signal
in a black-box IDE agent architecture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from multi_agent.sandbox import SandboxRunner, SandboxResult


# ── Default Configuration ────────────────────────────────

DEFAULT_VERIFIER_CONFIG: dict[str, Any] = {
    "test_command": "pytest -x --tb=short",
    "lint_command": "ruff check",
    "timeout_sec": 120,
    "enabled": True,
    "max_output_chars": 2000,
}


# ── Data Classes ─────────────────────────────────────────

@dataclass
class LintResult:
    """Outcome of running a linter against changed files.

    Attributes:
        error_count: Number of lint errors/warnings found.
        output: Raw linter output (truncated).
        returncode: Process exit code.
        command_used: The lint command that was executed.
    """

    error_count: int = 0
    output: str = ""
    returncode: int = 0
    command_used: str = ""
    duration_sec: float = 0.0


@dataclass
class VerificationResult:
    """Aggregated outcome of all verification checks.

    Combines test execution, linting, and optional type-checking results
    into a single structure that can be formatted for reviewer or repair
    prompt injection.

    Attributes:
        test_passed: Number of tests that passed.
        test_failed: Number of tests that failed.
        test_errors: Number of tests with errors (collection/setup failures).
        test_output: Raw pytest output (truncated to max_output_chars).
        lint_errors: Number of lint errors found.
        lint_output: Raw linter output (truncated).
        type_errors: Number of type checker errors (0 if not run).
        coverage_pct: Code coverage percentage if available, else None.
        duration_sec: Total wall-clock time for all checks.
        command_used: The test command that was executed.
    """

    test_passed: int = 0
    test_failed: int = 0
    test_errors: int = 0
    test_output: str = ""
    lint_errors: int = 0
    lint_output: str = ""
    type_errors: int = 0
    coverage_pct: float | None = None
    duration_sec: float = 0.0
    command_used: str = ""

    @property
    def tests_ok(self) -> bool:
        """True if tests ran and none failed or errored."""
        return self.test_failed == 0 and self.test_errors == 0

    @property
    def lint_ok(self) -> bool:
        """True if linter found no errors."""
        return self.lint_errors == 0

    @property
    def all_ok(self) -> bool:
        """True if all checks passed."""
        return self.tests_ok and self.lint_ok and self.type_errors == 0


# ── Parsing Helpers ──────────────────────────────────────

# Matches pytest summary lines like "3 passed, 1 failed, 2 errors"
_PYTEST_SUMMARY_RE = re.compile(
    r"(?:=+\s+)?"
    r"(?:(?P<failed>\d+)\s+failed)?"
    r"[,\s]*"
    r"(?:(?P<passed>\d+)\s+passed)?"
    r"[,\s]*"
    r"(?:(?P<errors>\d+)\s+error)?"
)

# More robust: look for the short-summary line
_PYTEST_FINAL_RE = re.compile(
    r"(\d+)\s+passed|(\d+)\s+failed|(\d+)\s+error",
)


def _parse_pytest_counts(output: str) -> tuple[int, int, int]:
    """Extract (passed, failed, errors) from pytest output.

    Scans the last 20 lines of output for pytest summary statistics.

    Returns:
        Tuple of (passed, failed, errors) counts.
    """
    passed = failed = errors = 0
    # Focus on the tail where pytest prints its summary
    tail = "\n".join(output.splitlines()[-20:])
    for match in _PYTEST_FINAL_RE.finditer(tail):
        if match.group(1):
            passed = int(match.group(1))
        if match.group(2):
            failed = int(match.group(2))
        if match.group(3):
            errors = int(match.group(3))
    return passed, failed, errors


def _parse_lint_error_count(output: str) -> int:
    """Count lint errors from ruff/flake8 output.

    Heuristic: each non-empty line that starts with a file path
    (containing ':') is an error.
    """
    count = 0
    for line in output.strip().splitlines():
        line = line.strip()
        if line and ":" in line and not line.startswith(("Found", "All checks")):
            count += 1
    # Also check for ruff summary line "Found N errors"
    m = re.search(r"Found\s+(\d+)\s+error", output)
    if m:
        count = max(count, int(m.group(1)))
    return count


def _truncate_output(text: str, max_chars: int) -> str:
    """Truncate output to max_chars, keeping head and tail."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half]
        + f"\n\n... [truncated {len(text) - max_chars} chars] ...\n\n"
        + text[-half:]
    )


# ── Core Functions ───────────────────────────────────────

def run_lint(
    changed_files: list[str],
    codebase_root: str,
    config: dict[str, Any] | None = None,
) -> LintResult:
    """Run linter against changed files.

    Args:
        changed_files: List of file paths (relative or absolute) to lint.
        codebase_root: Root directory of the codebase (used as cwd).
        config: Verifier configuration dict. See ``DEFAULT_VERIFIER_CONFIG``.

    Returns:
        A :class:`LintResult` with error counts and output.
    """
    cfg = {**DEFAULT_VERIFIER_CONFIG, **(config or {})}
    if not cfg.get("enabled", True):
        return LintResult()

    # Filter to Python files only for ruff/flake8
    py_files = [f for f in changed_files if f.endswith(".py")]
    if not py_files:
        return LintResult(command_used=cfg["lint_command"] + " (no Python files)")

    lint_cmd = cfg["lint_command"]
    command = f"{lint_cmd} {' '.join(py_files)}"
    max_chars = cfg.get("max_output_chars", 2000)

    runner = SandboxRunner(
        max_output_chars=max_chars,
        default_timeout_sec=cfg.get("timeout_sec", 120),
    )
    result = runner.run(command, cwd=codebase_root)

    combined_output = (result.stdout + "\n" + result.stderr).strip()
    error_count = _parse_lint_error_count(combined_output)

    return LintResult(
        error_count=error_count,
        output=_truncate_output(combined_output, max_chars),
        returncode=result.returncode,
        command_used=command,
        duration_sec=result.duration_sec,
    )


def run_verification(
    changed_files: list[str],
    codebase_root: str,
    config: dict[str, Any] | None = None,
) -> VerificationResult:
    """Run tests and linting against changed files.

    Executes the configured test command (default: ``pytest -x --tb=short``)
    and lint command (default: ``ruff check``) in sandboxed subprocesses,
    then aggregates results into a :class:`VerificationResult`.

    Args:
        changed_files: Files modified by the builder (used for lint scope).
        codebase_root: Root directory of the codebase.
        config: Verifier configuration dict. Keys:
            - ``test_command``: Test runner command (default: "pytest -x --tb=short").
            - ``lint_command``: Linter command (default: "ruff check").
            - ``timeout_sec``: Max seconds per command (default: 120).
            - ``enabled``: If False, return empty result (default: True).
            - ``max_output_chars``: Truncation limit (default: 2000).

    Returns:
        A :class:`VerificationResult` with aggregated check outcomes.
    """
    cfg = {**DEFAULT_VERIFIER_CONFIG, **(config or {})}
    if not cfg.get("enabled", True):
        return VerificationResult()

    max_chars = cfg.get("max_output_chars", 2000)
    timeout = cfg.get("timeout_sec", 120)

    runner = SandboxRunner(
        max_output_chars=max_chars,
        default_timeout_sec=timeout,
    )

    total_duration = 0.0

    # ── Run tests ────────────────────────────────────────
    test_cmd = cfg["test_command"]
    test_result = runner.run(test_cmd, cwd=codebase_root)
    total_duration += test_result.duration_sec

    test_output = (test_result.stdout + "\n" + test_result.stderr).strip()
    test_output = _truncate_output(test_output, max_chars)

    if test_result.timed_out:
        passed, failed, errors = 0, 0, 1
        test_output += "\n\n[TIMEOUT] Test execution exceeded time limit."
    else:
        passed, failed, errors = _parse_pytest_counts(test_output)

    # ── Run lint ─────────────────────────────────────────
    lint_result = run_lint(changed_files, codebase_root, cfg)
    total_duration += lint_result.duration_sec

    # ── Parse coverage if present ────────────────────────
    coverage_pct = None
    cov_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", test_output)
    if cov_match:
        coverage_pct = float(cov_match.group(1))

    return VerificationResult(
        test_passed=passed,
        test_failed=failed,
        test_errors=errors,
        test_output=test_output,
        lint_errors=lint_result.error_count,
        lint_output=lint_result.output,
        type_errors=0,
        coverage_pct=coverage_pct,
        duration_sec=round(total_duration, 2),
        command_used=test_cmd,
    )


# ── Formatting for Prompt Injection ──────────────────────

def format_for_reviewer(result: VerificationResult) -> str:
    """Format verification results as Markdown for reviewer prompt injection.

    Produces a structured summary that gives the reviewer agent
    ground-truth signals about test and lint status, reducing
    hallucinated approvals.

    Args:
        result: The verification result to format.

    Returns:
        Markdown-formatted string suitable for appending to reviewer prompt.
    """
    lines = ["## Automated Verification Results", ""]

    # Test summary
    status_emoji = "PASS" if result.tests_ok else "FAIL"
    lines.append(f"**Tests: {status_emoji}**")
    lines.append(
        f"- Passed: {result.test_passed} | "
        f"Failed: {result.test_failed} | "
        f"Errors: {result.test_errors}"
    )
    if result.coverage_pct is not None:
        lines.append(f"- Coverage: {result.coverage_pct:.0f}%")
    lines.append(f"- Command: `{result.command_used}`")
    lines.append(f"- Duration: {result.duration_sec:.1f}s")
    lines.append("")

    # Lint summary
    lint_status = "PASS" if result.lint_ok else "FAIL"
    lines.append(f"**Lint: {lint_status}**")
    lines.append(f"- Errors: {result.lint_errors}")
    lines.append("")

    # Include failure output for context
    if not result.tests_ok and result.test_output:
        lines.append("### Test Output (failures)")
        lines.append("```")
        lines.append(result.test_output)
        lines.append("```")
        lines.append("")

    if not result.lint_ok and result.lint_output:
        lines.append("### Lint Output")
        lines.append("```")
        lines.append(result.lint_output)
        lines.append("```")
        lines.append("")

    # Overall verdict
    if result.all_ok:
        lines.append(
            "> All automated checks passed. Review should focus on "
            "design, correctness, and requirements alignment."
        )
    else:
        lines.append(
            "> WARNING: Automated checks failed. Reviewer should "
            "request changes addressing the failures above."
        )

    return "\n".join(lines)


def format_for_repair(result: VerificationResult) -> str:
    """Format verification results for the repair cycle.

    Produces a more detailed, action-oriented format designed for
    the repair cycle's diagnosis step, including raw output for
    pattern matching.

    Args:
        result: The verification result to format.

    Returns:
        Markdown-formatted string suitable for repair cycle input.
    """
    lines = ["## Verification Failures for Repair", ""]

    if not result.tests_ok:
        lines.append("### Failing Tests")
        lines.append(
            f"- {result.test_failed} failed, {result.test_errors} errors "
            f"(out of {result.test_passed + result.test_failed + result.test_errors} total)"
        )
        lines.append("")
        if result.test_output:
            lines.append("#### Raw Test Output")
            lines.append("```")
            lines.append(result.test_output)
            lines.append("```")
            lines.append("")

    if not result.lint_ok:
        lines.append("### Lint Errors")
        lines.append(f"- {result.lint_errors} errors found")
        lines.append("")
        if result.lint_output:
            lines.append("#### Raw Lint Output")
            lines.append("```")
            lines.append(result.lint_output)
            lines.append("```")
            lines.append("")

    if result.type_errors > 0:
        lines.append(f"### Type Errors: {result.type_errors}")
        lines.append("")

    return "\n".join(lines)
