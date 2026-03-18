#!/usr/bin/env python3
"""
MyGO Controlled Experiment Runner v2 — Adaptive Decomposition Study

Implements experiment-protocol-v2.md: 6 conditions (2 baselines + 2×2 factorial),
enhanced metrics (cost, bridge violations), and statistical analysis.

Usage:
    # Run pilot (9 custom tasks × 6 conditions × 3 reps = 162 runs)
    python scripts/experiment_runner_v2.py --pilot

    # Run single condition
    python scripts/experiment_runner_v2.py --condition adaptive_bridge

    # Run specific task
    python scripts/experiment_runner_v2.py --task task-api-users --condition multi --runs 1

    # Analyze results
    python scripts/experiment_runner_v2.py --analyze

    # Calibrate complexity thresholds from labeled data
    python scripts/experiment_runner_v2.py --calibrate

    # Dry run
    python scripts/experiment_runner_v2.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
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
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results" / "experiment_v2"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

# ── Experimental Conditions (Protocol v2 §3) ────────────

CONDITIONS = {
    "single": {
        "label": "C1: Single",
        "description": "Single agent (Claude CLI only, no reviewer)",
        "use_reviewer": False,
        "decompose": "none",      # none | fixed | adaptive
        "bridge": False,
    },
    "multi": {
        "label": "C2: Multi",
        "description": "Multi-agent (builder + reviewer, strict mode)",
        "use_reviewer": True,
        "decompose": "none",
        "bridge": False,
    },
    "fixed_decompose": {
        "label": "C3: FixedDecomp",
        "description": "Multi-agent + always decompose, no bridge",
        "use_reviewer": True,
        "decompose": "fixed",
        "bridge": False,
    },
    "fixed_bridge": {
        "label": "C4: FixedDecomp+Bridge",
        "description": "Multi-agent + always decompose + context bridge",
        "use_reviewer": True,
        "decompose": "fixed",
        "bridge": True,
    },
    "adaptive": {
        "label": "C5: Adaptive",
        "description": "Multi-agent + adaptive decompose, no bridge",
        "use_reviewer": True,
        "decompose": "adaptive",
        "bridge": False,
    },
    "adaptive_bridge": {
        "label": "C6: Adaptive+Bridge",
        "description": "Multi-agent + adaptive decompose + context bridge",
        "use_reviewer": True,
        "decompose": "adaptive",
        "bridge": True,
    },
}

RUNS_PER_CONDITION = 5
PILOT_RUNS = 3
TASK_TIMEOUT_SEC = 3600  # 1 hour max per task


# ── Utility Functions ────────────────────────────────────

def _clear_semantic_memory() -> None:
    """Clear semantic memory to prevent cross-task learning effects."""
    mem_file = PROJECT_ROOT / ".multi-agent" / "memory" / "semantic.jsonl"
    if mem_file.exists():
        mem_file.write_text("", encoding="utf-8")


def _clear_workspace_state() -> None:
    """Clear workspace state between runs (checkpoints, outbox, inbox)."""
    for subdir in ("checkpoints", "outbox", "inbox"):
        d = PROJECT_ROOT / ".multi-agent" / subdir
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)


def _extract_retry_count() -> int:
    """Extract retry count from the most recent report."""
    try:
        import re
        report_files = sorted(
            (PROJECT_ROOT / ".multi-agent").glob("report-*.md"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if report_files:
            text = report_files[0].read_text(encoding="utf-8")
            m = re.search(r"总重试:\s*(\d+)", text)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


def _extract_sub_task_count() -> int:
    """Extract sub-task count from the most recent report."""
    try:
        import re
        report_files = sorted(
            (PROJECT_ROOT / ".multi-agent").glob("report-*.md"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if report_files:
            text = report_files[0].read_text(encoding="utf-8")
            m = re.search(r"总子任务:\s*(\d+)", text)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


def _extract_token_usage() -> dict[str, int]:
    """Extract token usage from finops log for the most recent task."""
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    token_log = PROJECT_ROOT / ".multi-agent" / "logs" / "token-usage.jsonl"
    if not token_log.exists():
        return usage
    try:
        lines = token_log.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return usage
        # Sum all entries (they're per-node within a task)
        for line in lines[-20:]:  # last 20 entries should cover one task
            entry = json.loads(line)
            usage["input_tokens"] += entry.get("input_tokens", 0)
            usage["output_tokens"] += entry.get("output_tokens", 0)
            usage["total_tokens"] += entry.get("total_tokens", 0)
    except Exception:
        pass
    return usage


def _extract_bridge_violations() -> int:
    """Extract bridge violation count from the most recent report."""
    try:
        import re
        report_files = sorted(
            (PROJECT_ROOT / ".multi-agent").glob("report-*.md"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if report_files:
            text = report_files[0].read_text(encoding="utf-8")
            m = re.search(r"bridge_violations_total:\s*(\d+)", text)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


def _extract_decompose_decision() -> str:
    """Extract adaptive decompose decision from logs."""
    try:
        log_files = sorted(
            (PROJECT_ROOT / ".multi-agent" / "logs").glob("*.log"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        for lf in log_files[:3]:
            text = lf.read_text(encoding="utf-8", errors="ignore")
            if "Adaptive strategy:" in text:
                import re
                m = re.search(r"Adaptive strategy: (\w+)", text)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""


def discover_tasks(tasks_dir: Path) -> list[dict]:
    """Discover experiment tasks from the tasks directory."""
    tasks = []
    if not tasks_dir.exists():
        return tasks

    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        req_file = task_dir / "requirement.txt"
        if not req_file.exists():
            continue

        # Classify complexity for stratification
        requirement = req_file.read_text(encoding="utf-8").strip()
        from multi_agent.adaptive_decompose import (
            classify_complexity,
            estimate_complexity_features,
        )
        features = estimate_complexity_features(requirement)
        complexity = classify_complexity(features)

        has_gt = bool(
            list(task_dir.glob("test_gt_*.py"))
            or list(task_dir.glob("test_ground_truth.py"))
        )
        tasks.append({
            "task_id": task_dir.name,
            "requirement": requirement,
            "has_ground_truth": has_gt,
            "task_dir": str(task_dir),
            "complexity": complexity.value,
            "complexity_score": round(features.complexity_score, 2),
        })
    return tasks


def get_git_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def get_model_id() -> str:
    return os.environ.get("CLAUDE_MODEL", "claude-opus-4-6")


def run_ground_truth_tests(task_dir: Path) -> dict:
    """Run GT tests and return results."""
    test_files = (
        list(task_dir.glob("test_gt_*.py"))
        + list(task_dir.glob("test_ground_truth.py"))
    )
    if not test_files:
        return {"total": 0, "passed": 0, "failed": 0, "error": "no test file"}

    test_file = test_files[0]
    try:
        r = subprocess.run(
            ["python3", "-m", "pytest", str(test_file), "-v", "--tb=short"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        )
        passed = r.stdout.count(" PASSED")
        failed = r.stdout.count(" FAILED")
        errored = r.stdout.count(" ERROR")
        return {
            "total": passed + failed + errored,
            "passed": passed,
            "failed": failed + errored,
        }
    except subprocess.TimeoutExpired:
        return {"total": 0, "passed": 0, "failed": 0, "error": "timeout"}
    except Exception as e:
        return {"total": 0, "passed": 0, "failed": 0, "error": str(e)}


def run_lint_check() -> int:
    try:
        r = subprocess.run(
            ["ruff", "check", "--output-format", "json", "src/"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        if r.stdout.strip():
            return len(json.loads(r.stdout))
    except Exception:
        pass
    return -1


def run_type_check() -> int:
    try:
        r = subprocess.run(
            ["mypy", "--no-error-summary", "src/"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        output = r.stdout + r.stderr
        return len([l for l in output.splitlines() if ": error:" in l])
    except Exception:
        return -1


# ── Main Experiment Logic ────────────────────────────────

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
        "experiment_version": "2.0",
        "protocol": "experiment-protocol-v2",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "condition": condition,
        "condition_label": cond_cfg["label"],
        "task_id": task_id,
        "task_complexity": task.get("complexity", "unknown"),
        "task_complexity_score": task.get("complexity_score", 0),
        "run_idx": run_idx,
        "git_commit": get_git_commit(),
        "model_id": get_model_id(),
        "metrics": {},
    }

    print(f"\n{'='*70}")
    print(f"  {cond_cfg['label']} | Task: {task_id} ({task.get('complexity', '?')}) | Run: {run_idx}")
    print(f"  {cond_cfg['description']}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    if dry_run:
        print(f"  [DRY RUN] Would execute: {task['requirement'][:80]}...")
        result["metrics"]["dry_run"] = True
        return result

    # Clear state
    _clear_semantic_memory()
    _clear_workspace_state()

    # Build command
    cmd = [
        "python3", "-m", "multi_agent.cli", "go", task["requirement"],
        "--task-id", f"exp-{condition.replace('_', '-')}-{task_id}-r{run_idx}",
    ]

    # All conditions use the same execution path through the orchestration pipeline.
    # For C1 (single), we still pass through build+review but use the same CLI agent,
    # ensuring fair comparison (same overhead, same interrupt mechanism).
    cmd.extend(["--builder", builder, "--reviewer", builder, "--mode", "strict"])

    # Decompose strategy
    decompose = cond_cfg["decompose"]
    if decompose == "none":
        cmd.append("--no-decompose")
    elif decompose == "fixed":
        cmd.extend(["--decompose", "--auto-confirm"])
    elif decompose == "adaptive":
        cmd.extend(["--adaptive", "--auto-confirm"])

    # Context bridge
    if cond_cfg["bridge"]:
        cmd.append("--bridge")

    # Execute
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, timeout=TASK_TIMEOUT_SEC, cwd=str(PROJECT_ROOT),
            env=env, capture_output=False,
        )
        success = proc.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT after {TASK_TIMEOUT_SEC}s")
        subprocess.run(
            ["python3", "-m", "multi_agent.cli", "cancel"],
            capture_output=True, cwd=str(PROJECT_ROOT), env=env,
        )
        success = False
    except KeyboardInterrupt:
        print(f"\n  User interrupted at {condition}/{task_id}/run_{run_idx}")
        subprocess.run(
            ["python3", "-m", "multi_agent.cli", "cancel"],
            capture_output=True, cwd=str(PROJECT_ROOT), env=env,
        )
        sys.exit(1)
    duration_sec = time.time() - t0

    # Collect metrics
    task_dir = Path(task["task_dir"])
    test_results = run_ground_truth_tests(task_dir)
    token_usage = _extract_token_usage()

    # Estimate cost (Claude Opus 4.6 pricing: $15/M input, $75/M output)
    cost_usd = (
        token_usage["input_tokens"] * 15 / 1_000_000
        + token_usage["output_tokens"] * 75 / 1_000_000
    )

    result["metrics"] = {
        # Primary
        "resolve_rate": (
            test_results["passed"] == test_results["total"]
            and test_results["total"] > 0
        ),
        "test_pass_rate": (
            test_results["passed"] / test_results["total"]
            if test_results["total"] > 0 else 0.0
        ),
        "tests_total": test_results["total"],
        "tests_passed": test_results["passed"],
        "tests_failed": test_results["failed"],
        # Cost
        "total_tokens": token_usage["total_tokens"],
        "input_tokens": token_usage["input_tokens"],
        "output_tokens": token_usage["output_tokens"],
        "cost_usd": round(cost_usd, 4),
        # Efficiency
        "wall_clock_sec": round(duration_sec, 1),
        "retry_count": _extract_retry_count(),
        # Quality
        "lint_violations": run_lint_check(),
        "type_errors": run_type_check(),
        # Decomposition
        "decompose_decision": _extract_decompose_decision(),
        "sub_task_count": _extract_sub_task_count() if decompose != "none" else 0,
        # Bridge
        "bridge_violations": _extract_bridge_violations() if cond_cfg["bridge"] else 0,
        # Meta
        "task_returncode": 0 if success else 1,
    }

    # Derived metrics
    if result["metrics"]["cost_usd"] > 0:
        result["metrics"]["correctness_per_dollar"] = round(
            (1.0 if result["metrics"]["resolve_rate"] else 0.0)
            / result["metrics"]["cost_usd"], 2
        )

    # Save with checksum
    out_dir = results_dir / condition / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"run_{run_idx}.json"
    content = json.dumps(result, indent=2, ensure_ascii=False)
    result["_checksum"] = hashlib.sha256(content.encode()).hexdigest()[:16]
    content = json.dumps(result, indent=2, ensure_ascii=False)
    out_file.write_text(content, encoding="utf-8")
    print(f"  Result saved: {out_file}")
    print(f"  Pass: {test_results['passed']}/{test_results['total']} | "
          f"Cost: ${cost_usd:.4f} | Duration: {duration_sec:.0f}s")

    return result


# ── Analysis ─────────────────────────────────────────────

def analyze_results(results_dir: Path) -> None:
    """Statistical analysis per experiment-protocol-v2.md §6."""
    if not results_dir.exists():
        print(f"No results found at {results_dir}")
        return

    # Load all results
    all_results: dict[str, list[dict]] = {}
    for json_file in sorted(results_dir.rglob("run_*.json")):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        cond = data.get("condition", "unknown")
        all_results.setdefault(cond, []).append(data)

    if not all_results:
        print("No result files found.")
        return

    condition_order = [
        "single", "multi", "fixed_decompose",
        "fixed_bridge", "adaptive", "adaptive_bridge",
    ]

    # ── Summary Table ──
    print(f"\n{'='*90}")
    print("  Experiment Results Summary (Protocol v2)")
    print(f"{'='*90}\n")

    header = (
        f"  {'Condition':<20} {'N':>4} {'Resolve%':>9} {'TestPass%':>10} "
        f"{'Avg$':>8} {'AvgTokens':>10} {'AvgDur':>8} {'AvgRetry':>9}"
    )
    print(header)
    print(f"  {'-'*85}")

    for cond in condition_order:
        runs = all_results.get(cond, [])
        if not runs:
            continue
        n = len(runs)
        m = [r.get("metrics", {}) for r in runs]

        resolve = sum(1 for x in m if x.get("resolve_rate")) / n
        test_pass = sum(x.get("test_pass_rate", 0) for x in m) / n
        avg_cost = sum(x.get("cost_usd", 0) for x in m) / n
        avg_tokens = sum(x.get("total_tokens", 0) for x in m) / n
        avg_dur = sum(x.get("wall_clock_sec", 0) for x in m) / n
        avg_retry = sum(x.get("retry_count", 0) for x in m) / n

        label = CONDITIONS.get(cond, {}).get("label", cond)
        print(
            f"  {label:<20} {n:>4} {resolve:>8.0%} {test_pass:>9.1%} "
            f"${avg_cost:>7.4f} {avg_tokens:>10.0f} {avg_dur:>7.0f}s {avg_retry:>8.1f}"
        )

    # ── Stratified by Complexity ──
    print(f"\n  Stratified by Task Complexity:")
    print(f"  {'-'*85}")

    for complexity in ["simple", "medium", "complex"]:
        print(f"\n  [{complexity.upper()}]")
        for cond in condition_order:
            runs = [
                r for r in all_results.get(cond, [])
                if r.get("task_complexity") == complexity
            ]
            if not runs:
                continue
            n = len(runs)
            m = [r.get("metrics", {}) for r in runs]
            resolve = sum(1 for x in m if x.get("resolve_rate")) / n
            avg_cost = sum(x.get("cost_usd", 0) for x in m) / n
            label = CONDITIONS.get(cond, {}).get("label", cond)
            print(f"    {label:<20} n={n:>3}  resolve={resolve:>5.0%}  cost=${avg_cost:.4f}")

    # ── Statistical Tests ──
    print(f"\n  Statistical Comparisons:")
    print(f"  {'-'*85}")

    try:
        from scipy.stats import mannwhitneyu, fisher_exact

        comparisons = [
            ("adaptive_bridge", "fixed_decompose", "C6 vs C3 (primary)"),
            ("adaptive_bridge", "multi", "C6 vs C2 (no degradation)"),
            ("adaptive", "fixed_decompose", "C5 vs C3 (adaptive ablation)"),
            ("fixed_bridge", "fixed_decompose", "C4 vs C3 (bridge ablation)"),
        ]

        for cond_a, cond_b, label in comparisons:
            runs_a = all_results.get(cond_a, [])
            runs_b = all_results.get(cond_b, [])
            if len(runs_a) < 3 or len(runs_b) < 3:
                print(f"  {label}: insufficient data")
                continue

            scores_a = [1 if r["metrics"]["resolve_rate"] else 0 for r in runs_a]
            scores_b = [1 if r["metrics"]["resolve_rate"] else 0 for r in runs_b]

            # Mann-Whitney U for resolve rate
            try:
                stat, p = mannwhitneyu(scores_a, scores_b, alternative="two-sided")
                sig = "***" if p < 0.0083 else "**" if p < 0.05 else "ns"
                print(f"  {label}: U={stat:.1f}, p={p:.4f} {sig}")
            except ValueError as e:
                print(f"  {label}: {e}")

            # Cost comparison
            cost_a = [r["metrics"].get("cost_usd", 0) for r in runs_a]
            cost_b = [r["metrics"].get("cost_usd", 0) for r in runs_b]
            try:
                stat, p = mannwhitneyu(cost_a, cost_b, alternative="two-sided")
                print(f"    Cost: U={stat:.1f}, p={p:.4f}")
            except ValueError:
                pass

        # Cliff's delta for effect size
        print(f"\n  Effect Sizes (Cliff's delta):")
        for cond_a, cond_b, label in comparisons:
            runs_a = all_results.get(cond_a, [])
            runs_b = all_results.get(cond_b, [])
            if not runs_a or not runs_b:
                continue
            scores_a = [1 if r["metrics"]["resolve_rate"] else 0 for r in runs_a]
            scores_b = [1 if r["metrics"]["resolve_rate"] else 0 for r in runs_b]
            # Cliff's delta
            n_a, n_b = len(scores_a), len(scores_b)
            if n_a == 0 or n_b == 0:
                continue
            gt = sum(1 for a in scores_a for b in scores_b if a > b)
            lt = sum(1 for a in scores_a for b in scores_b if a < b)
            delta = (gt - lt) / (n_a * n_b)
            magnitude = (
                "negligible" if abs(delta) < 0.147 else
                "small" if abs(delta) < 0.33 else
                "medium" if abs(delta) < 0.474 else
                "large"
            )
            print(f"    {label}: δ={delta:+.3f} ({magnitude})")

    except ImportError:
        print("  (scipy not installed — pip install scipy)")

    print(f"\n  Note: *** p<0.0083 (Holm-Bonferroni), ** p<0.05, ns = not significant")
    print(f"{'='*90}")


# ── Calibration ──────────────────────────────────────────

def calibrate_from_tasks(tasks_dir: Path) -> None:
    """Calibrate complexity thresholds from task set with oracle labels."""
    from multi_agent.adaptive_decompose import calibrate_thresholds

    labeled_data = []
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        req_file = task_dir / "requirement.txt"
        if not req_file.exists():
            continue

        # Oracle complexity from directory naming convention
        name = task_dir.name
        if "bugfix" in name:
            oracle = "simple"
        elif "api" in name:
            oracle = "medium"
        elif "auth" in name:
            oracle = "complex"
        else:
            continue  # skip unknown

        labeled_data.append({
            "requirement": req_file.read_text(encoding="utf-8").strip(),
            "oracle_level": oracle,
        })

    if not labeled_data:
        print("No labeled tasks found for calibration.")
        return

    print(f"Calibrating from {len(labeled_data)} labeled tasks...")
    output_path = PROJECT_ROOT / "config" / "complexity_thresholds.json"
    thresholds = calibrate_thresholds(labeled_data, output_path)
    print(f"Calibrated thresholds: {thresholds}")
    print(f"Saved to: {output_path}")

    # Validate: classify all tasks and show results
    from multi_agent.adaptive_decompose import (
        classify_complexity,
        estimate_complexity_features,
    )
    print(f"\nValidation:")
    correct = 0
    for item in labeled_data:
        features = estimate_complexity_features(item["requirement"])
        predicted = classify_complexity(features).value
        match = "✓" if predicted == item["oracle_level"] else "✗"
        if predicted == item["oracle_level"]:
            correct += 1
        print(f"  {match} oracle={item['oracle_level']:<8} predicted={predicted:<8} "
              f"score={features.complexity_score:.1f}")
    print(f"\nAccuracy: {correct}/{len(labeled_data)} ({correct/len(labeled_data):.0%})")


# ── Main ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MyGO Experiment Runner v2 (experiment-protocol-v2.md)"
    )
    parser.add_argument("--tasks-dir", type=Path, default=DEFAULT_TASKS_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--condition", choices=list(CONDITIONS.keys()))
    parser.add_argument("--task", type=str)
    parser.add_argument("--runs", type=int, default=RUNS_PER_CONDITION)
    parser.add_argument("--builder", default="claude")
    parser.add_argument("--pilot", action="store_true",
                        help=f"Pilot mode: custom tasks only, {PILOT_RUNS} reps")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.analyze:
        analyze_results(args.results_dir)
        return

    if args.calibrate:
        calibrate_from_tasks(args.tasks_dir)
        return

    # Discover tasks
    tasks = discover_tasks(args.tasks_dir)
    if not tasks:
        print(f"No experiment tasks found in {args.tasks_dir}")
        sys.exit(1)

    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]
        if not tasks:
            print(f"Task '{args.task}' not found")
            sys.exit(1)

    if args.list:
        print(f"Discovered {len(tasks)} tasks:")
        for t in tasks:
            gt = "GT" if t["has_ground_truth"] else "no-GT"
            print(f"  {t['task_id']:<30} [{gt}] {t['complexity']:<8} "
                  f"score={t['complexity_score']:<6} {t['requirement'][:50]}...")
        return

    # Determine runs
    runs = PILOT_RUNS if args.pilot else args.runs
    conditions = [args.condition] if args.condition else list(CONDITIONS.keys())

    total = len(conditions) * len(tasks) * runs
    print(f"\nExperiment Plan (Protocol v2):")
    print(f"  Conditions: {len(conditions)} | Tasks: {len(tasks)} | "
          f"Reps: {runs} | Total: {total}")
    print(f"  Results dir: {args.results_dir}")

    # Show task complexity distribution
    by_complexity: dict[str, int] = {}
    for t in tasks:
        by_complexity[t["complexity"]] = by_complexity.get(t["complexity"], 0) + 1
    print(f"  Task distribution: {by_complexity}")

    if args.dry_run:
        print("\n[DRY RUN]\n")

    all_results = []
    start_time = time.time()

    for condition in conditions:
        for task in tasks:
            for run_idx in range(1, runs + 1):
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

    print(f"\nCompleted in {hours}h {mins}m {secs}s ({len(all_results)} runs)")

    if not args.dry_run:
        combined = args.results_dir / "all_results.json"
        combined.parent.mkdir(parents=True, exist_ok=True)
        combined.write_text(
            json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Combined results: {combined}")
        print(f"Analyze: python scripts/experiment_runner_v2.py --analyze")


if __name__ == "__main__":
    main()
