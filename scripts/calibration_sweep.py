#!/usr/bin/env python3
"""
Calibration Sweep — Run single-agent baseline on a sample of SWE-bench tasks
to determine empirical solve rates, then select the final task set for the
main experiment.

Usage:
    # Phase 1: Run calibration (single agent, 1 rep each)
    python scripts/calibration_sweep.py --sweep --sample 60

    # Phase 2: Select tasks based on calibration results
    python scripts/calibration_sweep.py --select --target 40

    # Quick status check
    python scripts/calibration_sweep.py --status

Reference: experiment plan Phase 2
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "calibration"
SELECTED_TASKS_FILE = PROJECT_ROOT / "results" / "calibration" / "selected_tasks.json"

sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)


def run_sweep(sample_size: int = 60, model: str = "sonnet") -> None:
    """Run single-agent calibration on a stratified sample of SWE-bench tasks."""
    from swebench_adapter import load_swebench_tasks, _stratified_sample, setup_swebench_instance

    _log.info("Loading SWE-bench Verified dataset...")
    all_tasks = load_swebench_tasks(split="verified")
    _log.info("Total tasks available: %d", len(all_tasks))

    # Stratified sample
    sample = _stratified_sample(all_tasks, n=sample_size)
    _log.info("Sampled %d tasks (stratified by complexity)", len(sample))

    # Show distribution
    from collections import Counter
    dist = Counter(t.get("complexity", "unknown") for t in sample)
    _log.info("Complexity distribution: %s", dict(dist))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Set model via environment
    model_map = {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-6",
        "haiku": "claude-haiku-4-5-20251001",
    }
    model_id = model_map.get(model, model)
    os.environ["CLAUDE_MODEL"] = model_id

    completed = _load_completed_results()
    _log.info("Already completed: %d/%d", len(completed), len(sample))

    for i, task in enumerate(sample):
        task_id = task["task_id"]
        if task_id in completed:
            continue

        _log.info("[%d/%d] Running calibration for %s (%s)...",
                  i + 1, len(sample), task_id, task.get("complexity", "?"))

        try:
            # Setup instance
            workspace = setup_swebench_instance(task)

            # Run single-agent experiment
            result = _run_single_agent(task, workspace, model_id)

            # Save result
            result_file = RESULTS_DIR / f"{task_id}.json"
            result_file.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            _log.info("  Result: %s (%.1fs, $%.4f)",
                      "PASS" if result["resolved"] else "FAIL",
                      result["wall_clock_sec"],
                      result.get("cost_usd", 0))

        except KeyboardInterrupt:
            _log.info("Interrupted at task %d/%d", i + 1, len(sample))
            break
        except Exception as e:
            _log.error("  Error on %s: %s", task_id, e)
            # Save error result
            error_result = {
                "task_id": task_id,
                "instance_id": task["instance_id"],
                "complexity": task.get("complexity", "unknown"),
                "resolved": False,
                "error": str(e),
                "wall_clock_sec": 0,
            }
            result_file = RESULTS_DIR / f"{task_id}.json"
            result_file.write_text(
                json.dumps(error_result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    _print_status()


def _run_single_agent(
    task: dict, workspace: Path, model_id: str,
) -> dict:
    """Run a single-agent attempt on a SWE-bench task."""
    import subprocess

    from swebench_adapter import evaluate_swebench_instance

    task_file = workspace / "requirement.txt"
    if not task_file.exists():
        task_file.write_text(task["requirement"], encoding="utf-8")

    outbox_file = workspace / "outbox.json"
    if outbox_file.exists():
        outbox_file.unlink()

    # Clear token log for this run
    token_log = PROJECT_ROOT / ".multi-agent" / "logs" / "token-usage.jsonl"
    if token_log.exists():
        token_log.write_text("", encoding="utf-8")

    # Build command — single agent, no decomposition
    cmd = [
        "claude", "-p",
        f"You are solving a GitHub issue. Read the issue description below and "
        f"make the necessary code changes in the repository at {workspace}.\n\n"
        f"Issue:\n{task['requirement']}\n\n"
        f"Repository is already cloned at {workspace}. "
        f"Make your changes directly to the files. "
        f"Do NOT create new files unless necessary. "
        f"Focus on the minimal fix that resolves the issue.",
        "--allowedTools", "Read,Edit,Bash,Write",
        "--output-format", "json",
    ]

    t0 = time.time()
    try:
        r = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=1800,  # 30 min
            cwd=str(workspace),
        )
        duration = time.time() - t0

        # Extract token usage from JSON output
        cost_usd = 0.0
        total_tokens = 0
        if r.stdout.strip():
            try:
                output = json.loads(r.stdout)
                cost_usd = output.get("total_cost_usd", 0.0)
                usage = output.get("usage", {})
                total_tokens = (
                    usage.get("input_tokens", 0)
                    + usage.get("output_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )
            except (json.JSONDecodeError, ValueError):
                pass

    except subprocess.TimeoutExpired:
        duration = time.time() - t0
        return {
            "task_id": task["task_id"],
            "instance_id": task["instance_id"],
            "complexity": task.get("complexity", "unknown"),
            "resolved": False,
            "error": "timeout",
            "wall_clock_sec": duration,
            "cost_usd": 0,
            "total_tokens": 0,
        }

    # Evaluate
    eval_result = evaluate_swebench_instance(task, workspace=workspace)

    return {
        "task_id": task["task_id"],
        "instance_id": task["instance_id"],
        "complexity": task.get("complexity", "unknown"),
        "resolved": eval_result.get("resolve_rate", False),
        "tests_passed": eval_result.get("passed", 0),
        "tests_total": eval_result.get("total", 0),
        "wall_clock_sec": round(duration, 1),
        "cost_usd": round(cost_usd, 6),
        "total_tokens": total_tokens,
        "returncode": r.returncode if 'r' in dir() else -1,
    }


def _load_completed_results() -> dict[str, dict]:
    """Load all completed calibration results."""
    results = {}
    if RESULTS_DIR.exists():
        for f in RESULTS_DIR.glob("*.json"):
            if f.name == "selected_tasks.json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                results[data.get("task_id", f.stem)] = data
            except (json.JSONDecodeError, KeyError):
                continue
    return results


def _print_status() -> None:
    """Print calibration status summary."""
    results = _load_completed_results()
    if not results:
        print("No calibration results found.")
        return

    total = len(results)
    resolved = sum(1 for r in results.values() if r.get("resolved"))
    errors = sum(1 for r in results.values() if r.get("error"))
    total_cost = sum(r.get("cost_usd", 0) for r in results.values())

    print(f"\n{'='*70}")
    print(f"  Calibration Status: {total} tasks completed")
    print(f"  Resolve rate: {resolved}/{total} ({resolved/total*100:.1f}%)")
    print(f"  Errors: {errors}")
    print(f"  Total cost: ${total_cost:.2f}")
    print(f"{'='*70}")

    # By complexity
    from collections import defaultdict
    by_complexity = defaultdict(lambda: {"total": 0, "resolved": 0, "cost": 0})
    for r in results.values():
        c = r.get("complexity", "unknown")
        by_complexity[c]["total"] += 1
        by_complexity[c]["resolved"] += 1 if r.get("resolved") else 0
        by_complexity[c]["cost"] += r.get("cost_usd", 0)

    print(f"\n  By Complexity:")
    for c in ["simple", "medium", "complex", "unknown"]:
        if c in by_complexity:
            d = by_complexity[c]
            rate = d["resolved"] / d["total"] * 100 if d["total"] > 0 else 0
            print(f"    {c:>8}: {d['resolved']}/{d['total']} ({rate:.0f}%) | ${d['cost']:.2f}")


def select_tasks(target: int = 40) -> None:
    """Select final task set based on calibration results.

    Target distribution:
    - ~25% always-solved (ceiling control)
    - ~37.5% sometimes-solved (interesting zone, 40-70% expected solve rate)
    - ~37.5% never-solved (floor/hard)

    Since we only have 1 calibration run, we use complexity as a proxy:
    - simple tasks that passed → ceiling
    - complex tasks that passed → interesting zone (might fail with decomposition)
    - tasks that failed → floor
    """
    results = _load_completed_results()
    if not results:
        print("No calibration results. Run --sweep first.")
        return

    # Categorize
    ceiling = []   # solved + simple → always solvable
    interesting = []  # solved + medium/complex → might fail with decomposition
    floor_tasks = []  # failed → hard

    for task_id, r in results.items():
        if r.get("error") == "timeout":
            continue  # Skip infrastructure failures
        if r.get("resolved"):
            if r.get("complexity") == "simple":
                ceiling.append(r)
            else:
                interesting.append(r)
        else:
            floor_tasks.append(r)

    print(f"\nCalibration distribution:")
    print(f"  Ceiling (solved + simple): {len(ceiling)}")
    print(f"  Interesting (solved + medium/complex): {len(interesting)}")
    print(f"  Floor (failed): {len(floor_tasks)}")

    # Select
    import random
    random.seed(42)

    n_ceiling = max(5, target // 4)
    n_interesting = (target - n_ceiling) // 2
    n_floor = target - n_ceiling - n_interesting

    selected_ceiling = random.sample(ceiling, min(n_ceiling, len(ceiling)))
    selected_interesting = random.sample(interesting, min(n_interesting, len(interesting)))
    selected_floor = random.sample(floor_tasks, min(n_floor, len(floor_tasks)))

    selected = selected_ceiling + selected_interesting + selected_floor
    print(f"\nSelected {len(selected)} tasks:")
    print(f"  Ceiling: {len(selected_ceiling)}")
    print(f"  Interesting: {len(selected_interesting)}")
    print(f"  Floor: {len(selected_floor)}")

    # Estimate main experiment cost
    avg_cost = sum(r.get("cost_usd", 0.5) for r in selected) / len(selected) if selected else 0.5
    main_runs = len(selected) * 3 * 3  # 3 conditions x 3 reps
    est_cost = main_runs * avg_cost * 2  # decomposition ~2x cost multiplier
    print(f"\n  Avg calibration cost/task: ${avg_cost:.2f}")
    print(f"  Estimated main experiment cost: ${est_cost:.0f} ({main_runs} runs)")

    # Save
    SELECTED_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "selection_date": time.strftime("%Y-%m-%d"),
        "calibration_tasks": len(results),
        "selected_count": len(selected),
        "distribution": {
            "ceiling": len(selected_ceiling),
            "interesting": len(selected_interesting),
            "floor": len(selected_floor),
        },
        "tasks": [
            {
                "task_id": r["task_id"],
                "instance_id": r["instance_id"],
                "complexity": r.get("complexity", "unknown"),
                "calibration_resolved": r.get("resolved", False),
                "calibration_category": (
                    "ceiling" if r in selected_ceiling
                    else "interesting" if r in selected_interesting
                    else "floor"
                ),
            }
            for r in selected
        ],
    }
    SELECTED_TASKS_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n  Saved to: {SELECTED_TASKS_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibration sweep for SWE-bench experiment")
    parser.add_argument("--sweep", action="store_true", help="Run calibration sweep")
    parser.add_argument("--select", action="store_true", help="Select tasks from calibration results")
    parser.add_argument("--status", action="store_true", help="Print calibration status")
    parser.add_argument("--sample", type=int, default=60, help="Number of tasks to sample for calibration")
    parser.add_argument("--target", type=int, default=40, help="Number of tasks to select for main experiment")
    parser.add_argument("--model", default="sonnet", help="Model to use (sonnet/opus/haiku)")
    args = parser.parse_args()

    if args.sweep:
        run_sweep(sample_size=args.sample, model=args.model)
    elif args.select:
        select_tasks(target=args.target)
    elif args.status:
        _print_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
