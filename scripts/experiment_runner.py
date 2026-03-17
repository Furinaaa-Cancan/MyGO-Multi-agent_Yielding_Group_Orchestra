#!/usr/bin/env python3
"""
MyGO 对照实验运行器 (Controlled Experiment Runner)

按照 docs/experiment-protocol.md 设计，支持三种实验条件的对照实验。
每次运行在独立 git branch 上执行，采集多维度指标，输出结构化 JSON。

用法:
    # 运行全部 (3 条件 x 全部任务 x 3 重复)
    python scripts/experiment_runner.py --tasks-dir tasks/experiment

    # 只运行条件 B (multi)
    python scripts/experiment_runner.py --condition multi

    # 指定单个任务，单次运行
    python scripts/experiment_runner.py --task task-bugfix-01 --condition single --runs 1

    # 干跑
    python scripts/experiment_runner.py --dry-run

    # 分析已有结果
    python scripts/experiment_runner.py --analyze results/experiment
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_TASKS_DIR = PROJECT_ROOT / "tasks" / "experiment"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results" / "experiment"

# Experimental conditions as defined in experiment-protocol.md
CONDITIONS = {
    "single": {
        "description": "Single agent (Claude CLI only, no reviewer)",
        "use_reviewer": False,
        "decompose": False,
    },
    "multi": {
        "description": "Multi-agent (builder + reviewer, strict mode)",
        "use_reviewer": True,
        "decompose": False,
    },
    "decompose": {
        "description": "Multi-agent + task decomposition",
        "use_reviewer": True,
        "decompose": True,
    },
}

RUNS_PER_CONDITION = 3
TASK_TIMEOUT_SEC = 3600  # 1 hour max per task


def discover_tasks(tasks_dir: Path) -> list[dict]:
    """Discover experiment tasks from the tasks directory."""
    tasks = []
    if not tasks_dir.exists():
        return tasks

    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        req_file = task_dir / "requirement.txt"
        test_file = task_dir / "test_ground_truth.py"
        if not req_file.exists():
            print(f"  SKIP {task_dir.name}: missing requirement.txt")
            continue
        tasks.append({
            "task_id": task_dir.name,
            "requirement": req_file.read_text(encoding="utf-8").strip(),
            "has_ground_truth": test_file.exists(),
            "task_dir": str(task_dir),
        })
    return tasks


def get_git_commit() -> str:
    """Get current git commit hash."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def get_model_id() -> str:
    """Attempt to detect the Claude model ID being used."""
    # Could be extended to query the API; for now, return a placeholder
    return os.environ.get("CLAUDE_MODEL", "claude-opus-4-6")


def run_lint_check() -> int:
    """Run ruff check and return the number of violations."""
    try:
        r = subprocess.run(
            ["ruff", "check", "--output-format", "json", "src/"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        if r.stdout.strip():
            violations = json.loads(r.stdout)
            return len(violations)
    except Exception:
        pass
    return -1  # -1 means could not run


def run_type_check() -> int:
    """Run mypy and return the number of errors."""
    try:
        r = subprocess.run(
            ["mypy", "--no-error-summary", "src/"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        # Count lines that contain ": error:" in stderr/stdout
        output = r.stdout + r.stderr
        errors = [line for line in output.splitlines() if ": error:" in line]
        return len(errors)
    except Exception:
        return -1


def run_ground_truth_tests(task_dir: Path) -> dict:
    """Run the ground truth tests for a task and return results."""
    test_file = task_dir / "test_ground_truth.py"
    if not test_file.exists():
        return {"total": 0, "passed": 0, "failed": 0, "error": "no test file"}

    try:
        r = subprocess.run(
            ["python", "-m", "pytest", str(test_file), "-v", "--tb=short",
             "--json-report", "--json-report-file=-"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
        # Try to parse pytest-json-report output
        try:
            report = json.loads(r.stdout)
            summary = report.get("summary", {})
            return {
                "total": summary.get("total", 0),
                "passed": summary.get("passed", 0),
                "failed": summary.get("failed", 0),
            }
        except json.JSONDecodeError:
            pass

        # Fallback: parse pytest text output
        passed = r.stdout.count(" PASSED")
        failed = r.stdout.count(" FAILED")
        return {
            "total": passed + failed,
            "passed": passed,
            "failed": failed,
        }
    except subprocess.TimeoutExpired:
        return {"total": 0, "passed": 0, "failed": 0, "error": "timeout"}
    except Exception as e:
        return {"total": 0, "passed": 0, "failed": 0, "error": str(e)}


def count_changed_files() -> int:
    """Count the number of files changed since the last commit."""
    try:
        r = subprocess.run(
            ["git", "diff", "--stat", "HEAD~1"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        # Last line of git diff --stat is "N files changed, ..."
        lines = r.stdout.strip().splitlines()
        if lines:
            import re
            m = re.search(r"(\d+) files? changed", lines[-1])
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


def run_single_experiment(
    task: dict,
    condition: str,
    run_idx: int,
    builder: str,
    results_dir: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Execute a single experiment run and collect metrics."""
    task_id = task["task_id"]
    cond_cfg = CONDITIONS[condition]

    result = {
        "experiment_version": "1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "condition": condition,
        "task_id": task_id,
        "run_idx": run_idx,
        "git_commit": get_git_commit(),
        "model_id": get_model_id(),
        "metrics": {},
    }

    print(f"\n{'='*60}")
    print(f"  Condition: {condition} | Task: {task_id} | Run: {run_idx}")
    print(f"  {cond_cfg['description']}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    if dry_run:
        print(f"  [DRY RUN] Would execute: {task['requirement'][:80]}...")
        result["metrics"]["dry_run"] = True
        return result

    # Build the command
    cmd = ["my", "go", task["requirement"], "--task-id", f"exp-{condition}-{task_id}-r{run_idx}"]

    if cond_cfg["use_reviewer"]:
        cmd.extend(["--builder", builder, "--reviewer", builder, "--mode", "strict"])
    else:
        # Single mode: use builder only, no reviewer (pass-through review)
        cmd.extend(["--builder", builder])

    if cond_cfg["decompose"]:
        cmd.append("--decompose")

    # Execute
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, timeout=TASK_TIMEOUT_SEC, cwd=str(PROJECT_ROOT))
        success = proc.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT after {TASK_TIMEOUT_SEC}s")
        subprocess.run(["my", "cancel"], capture_output=True, cwd=str(PROJECT_ROOT))
        success = False
    except KeyboardInterrupt:
        print(f"\n  User interrupted at {condition}/{task_id}/run_{run_idx}")
        subprocess.run(["my", "cancel"], capture_output=True, cwd=str(PROJECT_ROOT))
        sys.exit(1)
    duration_sec = time.time() - t0

    # Collect metrics
    task_dir = Path(task["task_dir"])
    test_results = run_ground_truth_tests(task_dir)
    lint_violations = run_lint_check()
    type_errors = run_type_check()

    result["metrics"] = {
        "functional_pass": test_results["passed"] == test_results["total"] and test_results["total"] > 0,
        "functional_tests_total": test_results["total"],
        "functional_tests_passed": test_results["passed"],
        "lint_violations": lint_violations,
        "type_errors": type_errors,
        "retry_count": 0,  # TODO: extract from graph state
        "duration_sec": round(duration_sec, 1),
        "changed_files_count": count_changed_files(),
        "task_returncode": 0 if success else 1,
        "decompose_sub_tasks": 0,  # TODO: extract from decompose result
    }

    # Save individual result
    out_dir = results_dir / condition / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"run_{run_idx}.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Result saved: {out_file}")

    return result


def analyze_results(results_dir: Path) -> None:
    """Analyze experiment results and print summary statistics."""
    if not results_dir.exists():
        print(f"No results found at {results_dir}")
        return

    # Collect all results
    all_results: dict[str, list[dict]] = {}
    for json_file in sorted(results_dir.rglob("run_*.json")):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        cond = data.get("condition", "unknown")
        all_results.setdefault(cond, []).append(data)

    if not all_results:
        print("No result files found.")
        return

    print(f"\n{'='*70}")
    print("  Experiment Results Summary")
    print(f"{'='*70}\n")

    header = f"  {'Condition':<15} {'Runs':>5} {'Pass Rate':>10} {'Avg Duration':>13} {'Avg Lint':>9} {'Avg Type Err':>12}"
    print(header)
    print(f"  {'-'*65}")

    for cond in ["single", "multi", "decompose"]:
        runs = all_results.get(cond, [])
        if not runs:
            print(f"  {cond:<15} {'(no data)':>5}")
            continue

        n = len(runs)
        pass_count = sum(1 for r in runs if r.get("metrics", {}).get("functional_pass"))
        pass_rate = pass_count / n if n > 0 else 0

        avg_dur = sum(r.get("metrics", {}).get("duration_sec", 0) for r in runs) / n
        avg_lint = sum(r.get("metrics", {}).get("lint_violations", 0) for r in runs) / n
        avg_type = sum(r.get("metrics", {}).get("type_errors", 0) for r in runs) / n

        print(f"  {cond:<15} {n:>5} {pass_rate:>9.0%} {avg_dur:>11.1f}s {avg_lint:>9.1f} {avg_type:>12.1f}")

    # Pairwise comparisons (if scipy available)
    print(f"\n  Pairwise Comparisons:")
    print(f"  {'-'*65}")

    try:
        from scipy.stats import wilcoxon, mannwhitneyu

        for cond_a, cond_b in [("single", "multi"), ("multi", "decompose")]:
            runs_a = all_results.get(cond_a, [])
            runs_b = all_results.get(cond_b, [])
            if len(runs_a) < 3 or len(runs_b) < 3:
                print(f"  {cond_a} vs {cond_b}: insufficient data (need >= 3 per condition)")
                continue

            # Compare functional pass rates
            scores_a = [1 if r["metrics"]["functional_pass"] else 0 for r in runs_a]
            scores_b = [1 if r["metrics"]["functional_pass"] else 0 for r in runs_b]

            # Mann-Whitney U (independent samples)
            try:
                stat, p = mannwhitneyu(scores_a, scores_b, alternative="two-sided")
                sig = "***" if p < 0.0167 else "**" if p < 0.05 else "ns"
                print(f"  {cond_a} vs {cond_b} (pass rate): U={stat:.1f}, p={p:.4f} {sig}")
            except ValueError as e:
                print(f"  {cond_a} vs {cond_b} (pass rate): {e}")

            # Compare durations
            dur_a = [r["metrics"]["duration_sec"] for r in runs_a]
            dur_b = [r["metrics"]["duration_sec"] for r in runs_b]
            try:
                stat, p = mannwhitneyu(dur_a, dur_b, alternative="two-sided")
                sig = "***" if p < 0.0167 else "**" if p < 0.05 else "ns"
                print(f"  {cond_a} vs {cond_b} (duration):  U={stat:.1f}, p={p:.4f} {sig}")
            except ValueError as e:
                print(f"  {cond_a} vs {cond_b} (duration): {e}")

    except ImportError:
        print("  (scipy not installed — install with: pip install scipy)")
        print("  Skipping statistical tests.")

    # Effect size: Cliff's delta
    print(f"\n  Note: *** p<0.0167 (Bonferroni), ** p<0.05, ns = not significant")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(
        description="MyGO Controlled Experiment Runner (see docs/experiment-protocol.md)"
    )
    parser.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS_DIR,
                        help="Directory containing experiment tasks")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR,
                        help="Directory to save results")
    parser.add_argument("--condition", choices=list(CONDITIONS.keys()),
                        help="Run only this condition (default: all)")
    parser.add_argument("--task", type=str, help="Run only this task ID")
    parser.add_argument("--runs", type=int, default=RUNS_PER_CONDITION,
                        help=f"Repetitions per (condition, task) pair (default: {RUNS_PER_CONDITION})")
    parser.add_argument("--builder", default="claude", help="Builder agent (default: claude)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    parser.add_argument("--analyze", type=Path, nargs="?", const=DEFAULT_RESULTS_DIR,
                        help="Analyze existing results instead of running")
    parser.add_argument("--list", action="store_true", help="List discovered tasks")
    args = parser.parse_args()

    # Analyze mode
    if args.analyze is not None:
        analyze_results(args.analyze)
        return

    # Discover tasks
    tasks = discover_tasks(args.tasks_dir)
    if not tasks:
        print(f"No experiment tasks found in {args.tasks_dir}")
        print(f"Create tasks following docs/experiment-protocol.md section 4.2")
        sys.exit(1)

    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]
        if not tasks:
            print(f"Task '{args.task}' not found")
            sys.exit(1)

    if args.list:
        print(f"Discovered {len(tasks)} experiment tasks:")
        for t in tasks:
            gt = "GT" if t["has_ground_truth"] else "no-GT"
            print(f"  {t['task_id']:<30} [{gt}] {t['requirement'][:60]}...")
        return

    # Determine conditions to run
    conditions = [args.condition] if args.condition else list(CONDITIONS.keys())

    total_runs = len(conditions) * len(tasks) * args.runs
    print(f"Experiment plan: {len(conditions)} conditions x {len(tasks)} tasks x {args.runs} runs = {total_runs} total")
    print(f"Results will be saved to: {args.results_dir}")

    if args.dry_run:
        print("\n[DRY RUN MODE]\n")

    all_results = []
    start_time = time.time()

    for condition in conditions:
        for task in tasks:
            for run_idx in range(1, args.runs + 1):
                result = run_single_experiment(
                    task=task,
                    condition=condition,
                    run_idx=run_idx,
                    builder=args.builder,
                    results_dir=args.results_dir,
                    dry_run=args.dry_run,
                )
                all_results.append(result)

    elapsed = time.time() - start_time
    hours, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)

    print(f"\nExperiment completed in {hours}h {mins}m {secs}s")
    print(f"Total runs: {len(all_results)}")

    if not args.dry_run:
        # Save combined results
        combined = args.results_dir / "all_results.json"
        combined.parent.mkdir(parents=True, exist_ok=True)
        combined.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Combined results: {combined}")
        print(f"\nRun analysis with: python scripts/experiment_runner.py --analyze")


if __name__ == "__main__":
    main()
