#!/usr/bin/env python3
"""Automated evaluation framework for benchmark trials.

Runs gold-standard tests against agent output, computes quality scores,
and records results to the benchmark database.

Scoring formula (0-100):
  30 * test_pass_rate     (% of gold tests passing)
  20 * lint_clean         (1 if ruff passes, else 0)
  15 * builds_clean       (1 if Python imports succeed)
  15 * structure_score    (file organization quality)
  10 * completeness_score (required features present)
  10 * security_score     (no obvious vulnerabilities)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalResult:
    """Complete evaluation result for one trial."""

    task_id: str
    workspace: str

    # Raw results
    test_total: int = 0
    test_passed: int = 0
    test_failed: int = 0
    test_errors: int = 0
    test_output: str = ""

    lint_errors: int = 0
    lint_output: str = ""

    build_errors: list[str] = field(default_factory=list)

    # Scores (0.0 - 1.0)
    test_pass_rate: float = 0.0
    lint_clean: float = 0.0
    builds_clean: float = 0.0
    structure_score: float = 0.0
    completeness_score: float = 0.0
    security_score: float = 1.0  # Default to clean

    # Complexity & coverage
    avg_complexity: float = 0.0
    complexity_grade: str = ""
    coverage_pct: float = 0.0

    # Composite
    quality_score: float = 0.0

    # Check results (for quality_gates table)
    checks: dict[str, bool] = field(default_factory=dict)

    def compute_quality_score(self) -> float:
        base = (
            30 * self.test_pass_rate
            + 20 * self.lint_clean
            + 15 * self.builds_clean
            + 15 * self.structure_score
            + 10 * self.completeness_score
            + 10 * self.security_score
        )
        # Complexity bonus: up to 5 points if avg_complexity <= 5 (grade A/B)
        complexity_bonus = 0.0
        if self.avg_complexity > 0 and self.avg_complexity <= 5:
            # Linear scale: complexity 1 -> 5 pts, complexity 5 -> 1 pt
            complexity_bonus = max(1.0, 5.0 - (self.avg_complexity - 1))
        self.quality_score = min(100.0, base + complexity_bonus)
        return self.quality_score

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_gold_tests(workspace: Path, test_dir: Path, timeout: int = 120) -> dict[str, Any]:
    """Run gold-standard pytest tests against agent workspace.

    Runs pytest on gold test files directly, with PYTHONPATH set to workspace
    so that imports resolve to agent code.
    Returns {total, passed, failed, errors, output}.
    """
    if not test_dir.exists() or not list(test_dir.glob("test_*.py")):
        return {"total": 0, "passed": 0, "failed": 0, "errors": 0, "output": "no gold tests found"}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_dir), "-v", "--tb=short", "-q",
             "--override-ini=addopts=", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
            env={**os.environ, "PYTHONPATH": str(workspace)},
        )
        output = result.stdout + result.stderr

        # Parse pytest output
        # Pattern: "X passed, Y failed, Z errors" or "X passed"
        passed = failed = errors = 0
        for line in output.splitlines():
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", line)
            if m:
                failed = int(m.group(1))
            m = re.search(r"(\d+) error", line)
            if m:
                errors = int(m.group(1))

        total = passed + failed + errors
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "output": output[-2000:],  # Last 2000 chars
        }
    except subprocess.TimeoutExpired:
        return {"total": 0, "passed": 0, "failed": 0, "errors": 1, "output": "pytest timeout"}
    except Exception as e:
        return {"total": 0, "passed": 0, "failed": 0, "errors": 1, "output": str(e)}


def check_lint(workspace: Path, timeout: int = 30) -> dict[str, Any]:
    """Run ruff linter on workspace Python files."""
    py_files = list(workspace.rglob("*.py"))
    py_files = [f for f in py_files if "_gold_tests" not in str(f) and "__pycache__" not in str(f)]

    if not py_files:
        return {"errors": 0, "output": "no Python files found", "clean": True}

    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select=E,W,F", "--no-fix", str(workspace)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        error_count = result.stdout.count("\n") if result.stdout.strip() else 0
        return {
            "errors": error_count,
            "output": (result.stdout + result.stderr)[-1000:],
            "clean": result.returncode == 0,
        }
    except Exception as e:
        return {"errors": -1, "output": str(e), "clean": False}


def check_builds(workspace: Path) -> dict[str, Any]:
    """Check if Python files in workspace can be imported without errors."""
    errors = []
    py_files = list(workspace.rglob("*.py"))
    py_files = [f for f in py_files if not f.name.startswith("test_")
                and "_gold_tests" not in str(f) and "__pycache__" not in str(f)]

    for pf in py_files:
        try:
            result = subprocess.run(
                [sys.executable, "-c", f"import ast; ast.parse(open('{pf}').read())"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                errors.append(f"{pf.name}: {result.stderr.strip()[:200]}")
        except Exception as e:
            errors.append(f"{pf.name}: {e}")

    return {"errors": errors, "clean": len(errors) == 0}


def check_structure(workspace: Path, metadata: dict[str, Any]) -> float:
    """Score file organization quality (0.0-1.0)."""
    py_files = [f for f in workspace.rglob("*.py")
                if "__pycache__" not in str(f) and "_gold_tests" not in str(f)]

    if not py_files:
        return 0.0

    score = 0.0
    total_checks = 5

    # 1. Has at least one Python file
    if py_files:
        score += 1

    # 2. Has test files
    test_files = [f for f in py_files if f.name.startswith("test_")]
    if test_files:
        score += 1

    # 3. Reasonable file count (not everything in one file)
    expected_files = metadata.get("expected_file_count", 2)
    non_test = [f for f in py_files if not f.name.startswith("test_")]
    if len(non_test) >= min(expected_files, 2):
        score += 1

    # 4. No excessively long files (> 500 lines)
    long_files = 0
    for f in py_files:
        try:
            lines = len(f.read_text(encoding="utf-8").splitlines())
            if lines > 500:
                long_files += 1
        except Exception:
            pass
    if long_files == 0:
        score += 1

    # 5. Proper naming (snake_case files)
    bad_names = [f for f in py_files if not re.match(r"^[a-z_][a-z0-9_]*\.py$", f.name)]
    if not bad_names:
        score += 1

    return score / total_checks


def check_completeness(workspace: Path, metadata: dict[str, Any]) -> float:
    """Check if required features are present in the code (0.0-1.0)."""
    required_patterns = metadata.get("required_patterns", [])
    if not required_patterns:
        return 1.0  # No requirements specified = assume complete

    # Read all Python source
    all_code = ""
    for pf in workspace.rglob("*.py"):
        if "__pycache__" not in str(pf) and "_gold_tests" not in str(pf):
            try:
                all_code += pf.read_text(encoding="utf-8") + "\n"
            except Exception:
                pass

    if not all_code:
        return 0.0

    found = 0
    for pattern in required_patterns:
        if re.search(pattern, all_code, re.IGNORECASE):
            found += 1

    return found / len(required_patterns)


def check_security(workspace: Path) -> float:
    """Basic security check (0.0-1.0). Penalizes obvious vulnerabilities."""
    issues = 0
    total_checks = 4

    all_code = ""
    for pf in workspace.rglob("*.py"):
        if "__pycache__" not in str(pf) and "_gold_tests" not in str(pf):
            try:
                all_code += pf.read_text(encoding="utf-8") + "\n"
            except Exception:
                pass

    if not all_code:
        return 1.0

    # 1. No hardcoded passwords/secrets
    secret_patterns = [
        r'password\s*=\s*["\'][^"\']{3,}["\']',
        r'secret\s*=\s*["\'][^"\']{3,}["\']',
        r'api_key\s*=\s*["\'][^"\']{3,}["\']',
    ]
    for pat in secret_patterns:
        if re.search(pat, all_code, re.IGNORECASE):
            issues += 1
            break

    # 2. No eval/exec on user input
    if re.search(r'\b(eval|exec)\s*\(', all_code):
        issues += 1

    # 3. No SQL injection patterns
    if re.search(r'execute\s*\(\s*[f"\'].*\{', all_code):
        issues += 1

    # 4. No shell injection
    if re.search(r'subprocess\..*shell\s*=\s*True', all_code):
        issues += 1

    return (total_checks - issues) / total_checks


def check_complexity(workspace: Path, timeout: int = 30) -> dict[str, Any]:
    """Compute cyclomatic complexity of Python files using radon.

    Returns {"avg_complexity": float, "grade": str, "details": str}.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "radon", "cc", "-s", "-a", "-j", str(workspace)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0 and "No module named radon" in result.stderr:
            return {"avg_complexity": 0, "grade": "N/A", "details": "radon not installed"}

        output = result.stdout.strip()
        if not output:
            return {"avg_complexity": 0, "grade": "N/A", "details": "no output from radon"}

        # Parse JSON output from radon
        data = json.loads(output)

        # Compute average complexity across all functions/methods
        total_complexity = 0.0
        count = 0
        for file_path, blocks in data.items():
            for block in blocks:
                total_complexity += block.get("complexity", 0)
                count += 1

        if count == 0:
            return {"avg_complexity": 0, "grade": "N/A", "details": "no functions found"}

        avg = total_complexity / count

        # Determine grade based on radon's scale
        if avg <= 5:
            grade = "A"
        elif avg <= 10:
            grade = "B"
        elif avg <= 20:
            grade = "C"
        elif avg <= 30:
            grade = "D"
        elif avg <= 40:
            grade = "E"
        else:
            grade = "F"

        return {
            "avg_complexity": round(avg, 2),
            "grade": grade,
            "details": f"{count} blocks analyzed, avg complexity {avg:.2f} ({grade})",
        }
    except FileNotFoundError:
        return {"avg_complexity": 0, "grade": "N/A", "details": "radon not installed"}
    except Exception as e:
        return {"avg_complexity": 0, "grade": "N/A", "details": str(e)}


def check_coverage(workspace: Path, test_dir: Path, timeout: int = 120) -> dict[str, Any]:
    """Run pytest with coverage and return coverage percentage.

    Returns {"coverage_pct": float, "details": str}.
    """
    if not test_dir.exists() or not list(test_dir.glob("test_*.py")):
        return {"coverage_pct": 0, "details": "no test files found"}

    cov_json = workspace / "coverage.json"
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", str(test_dir),
                f"--cov={workspace}",
                "--cov-report=json",
                "-q",
                "--override-ini=addopts=",
                "-p", "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(workspace),
            env={**os.environ, "PYTHONPATH": str(workspace), "COVERAGE_FILE": str(workspace / ".coverage")},
        )
        if "No module named" in result.stderr and "pytest_cov" in result.stderr:
            return {"coverage_pct": 0, "details": "pytest-cov not installed"}

        # pytest-cov writes coverage.json in cwd by default
        cov_file = workspace / "coverage.json"
        if not cov_file.exists():
            # Try current directory
            cov_file = Path("coverage.json")

        if cov_file.exists():
            cov_data = json.loads(cov_file.read_text(encoding="utf-8"))
            pct = cov_data.get("totals", {}).get("percent_covered", 0.0)
            return {
                "coverage_pct": round(pct, 2),
                "details": f"{pct:.1f}% coverage",
            }
        else:
            return {"coverage_pct": 0, "details": "coverage.json not found"}
    except subprocess.TimeoutExpired:
        return {"coverage_pct": 0, "details": "coverage run timed out"}
    except FileNotFoundError:
        return {"coverage_pct": 0, "details": "pytest-cov not installed"}
    except Exception as e:
        return {"coverage_pct": 0, "details": str(e)}
    finally:
        # Clean up coverage artifacts
        for f in [workspace / "coverage.json", workspace / ".coverage"]:
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


def evaluate_trial(
    task_dir: Path,
    workspace: Path,
    task_id: str = "",
) -> EvalResult:
    """Run full evaluation suite on a trial's output.

    Args:
        task_dir: Path to benchmark task (contains tests/, metadata.yaml)
        workspace: Path to agent's output workspace
        task_id: Identifier for this trial

    Returns:
        EvalResult with all scores computed
    """
    # Load task metadata
    meta_path = task_dir / "metadata.yaml"
    metadata: dict[str, Any] = {}
    if meta_path.exists():
        import yaml
        metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}

    result = EvalResult(task_id=task_id, workspace=str(workspace))

    # 1. Gold-standard tests
    test_dir = task_dir / "tests"
    test_result = run_gold_tests(workspace, test_dir)
    result.test_total = test_result["total"]
    result.test_passed = test_result["passed"]
    result.test_failed = test_result["failed"]
    result.test_errors = test_result["errors"]
    result.test_output = test_result["output"]
    result.test_pass_rate = (
        test_result["passed"] / test_result["total"]
        if test_result["total"] > 0
        else 0.0
    )

    # 2. Lint
    lint_result = check_lint(workspace)
    result.lint_errors = lint_result["errors"]
    result.lint_output = lint_result["output"]
    result.lint_clean = 1.0 if lint_result["clean"] else 0.0

    # 3. Build
    build_result = check_builds(workspace)
    result.build_errors = build_result["errors"]
    result.builds_clean = 1.0 if build_result["clean"] else 0.0

    # 4. Structure
    result.structure_score = check_structure(workspace, metadata)

    # 5. Completeness
    result.completeness_score = check_completeness(workspace, metadata)

    # 6. Security
    result.security_score = check_security(workspace)

    # 7. Complexity
    complexity_result = check_complexity(workspace)
    result.avg_complexity = complexity_result["avg_complexity"]
    result.complexity_grade = complexity_result["grade"]

    # 8. Coverage
    coverage_result = check_coverage(workspace, test_dir)
    result.coverage_pct = coverage_result["coverage_pct"]

    # Composite score
    result.compute_quality_score()

    # Check summary for quality_gates table
    result.checks = {
        "gold_tests": result.test_pass_rate == 1.0,
        "lint": result.lint_clean == 1.0,
        "builds": result.builds_clean == 1.0,
        "structure": result.structure_score >= 0.6,
        "completeness": result.completeness_score >= 0.8,
        "security": result.security_score >= 0.75,
    }

    return result


def print_report(result: EvalResult) -> None:
    """Print a human-readable evaluation report."""
    print(f"\n{'='*60}")
    print(f"  Evaluation Report: {result.task_id}")
    print(f"  Workspace: {result.workspace}")
    print(f"{'='*60}\n")

    print(f"  Tests:        {result.test_passed}/{result.test_total} passed "
          f"({result.test_pass_rate*100:.0f}%) "
          f"{'PASS' if result.test_pass_rate == 1.0 else 'FAIL'}")
    print(f"  Lint:         {result.lint_errors} errors "
          f"{'PASS' if result.lint_clean else 'FAIL'}")
    print(f"  Build:        {'PASS' if result.builds_clean else 'FAIL'} "
          f"({len(result.build_errors)} errors)")
    print(f"  Structure:    {result.structure_score*100:.0f}%")
    print(f"  Completeness: {result.completeness_score*100:.0f}%")
    print(f"  Security:     {result.security_score*100:.0f}%")
    print(f"  Complexity:   {result.avg_complexity:.2f} (grade {result.complexity_grade})")
    print(f"  Coverage:     {result.coverage_pct:.1f}%")
    print(f"\n  {'─'*40}")
    print(f"  QUALITY SCORE: {result.quality_score:.1f} / 100")
    print(f"  {'─'*40}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate benchmark trial output")
    parser.add_argument("--task-dir", required=True, help="Path to benchmark task")
    parser.add_argument("--workspace", required=True, help="Path to agent output")
    parser.add_argument("--task-id", default="manual", help="Trial identifier")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of report")
    args = parser.parse_args()

    result = evaluate_trial(
        task_dir=Path(args.task_dir),
        workspace=Path(args.workspace),
        task_id=args.task_id,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_report(result)
