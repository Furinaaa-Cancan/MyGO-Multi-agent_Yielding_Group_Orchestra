#!/usr/bin/env python3
"""Benchmark experiment runner.

Orchestrates benchmark trials: runs agent(s) on tasks, evaluates output,
records everything to the benchmark SQLite database.

Usage:
  # Run single trial
  python runner.py --task low-01-fizzbuzz --mode multi --builder mock --reviewer mock

  # Run full experiment
  python runner.py --experiment "test run" --mode both --builder mock --reviewer mock

  # Dry run (evaluate existing workspace without running agents)
  python runner.py --task low-01-fizzbuzz --eval-only --workspace /path/to/output
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Add project to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from multi_agent.benchmark import (
    backfill_finops,
    complete_trial,
    create_experiment,
    create_trial,
    init_db,
    record_agent_run,
    record_quality_gate,
)
from evaluator import EvalResult, evaluate_trial, print_report


BENCHMARK_DIR = Path(__file__).resolve().parent
TASKS_DIR = BENCHMARK_DIR / "tasks"


def discover_tasks() -> list[Path]:
    """Find all benchmark task directories."""
    tasks = []
    for d in sorted(TASKS_DIR.iterdir()):
        if d.is_dir() and (d / "REQUIREMENT.md").exists():
            tasks.append(d)
    return tasks


def run_agent_on_task(
    task_dir: Path,
    workspace: Path,
    builder: str,
    reviewer: str | None = None,
    mode: str = "multi",
    timeout: int = 300,
) -> dict:
    """Run agent(s) on a benchmark task via `my go`.

    Returns orchestrator result dict with task_id, status, timing, etc.
    """
    requirement = (task_dir / "REQUIREMENT.md").read_text(encoding="utf-8")

    # Prepare workspace
    workspace.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        "my", "go", requirement,
        "--builder", builder,
        "--timeout", str(timeout),
        "--mode", "normal",  # avoid strict auth checks for benchmarking
    ]
    if mode == "multi" and reviewer:
        cmd.extend(["--reviewer", reviewer])
    elif mode == "single":
        cmd.extend(["--reviewer", builder])  # same agent reviews itself

    print(f"  Running: {' '.join(cmd[:6])}...")

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 30,
            cwd=str(_PROJECT_ROOT),
        )
        elapsed = time.time() - start

        return {
            "success": result.returncode == 0,
            "elapsed_sec": elapsed,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-1000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "elapsed_sec": time.time() - start,
            "stdout": "",
            "stderr": "timeout",
        }
    except Exception as e:
        return {
            "success": False,
            "elapsed_sec": time.time() - start,
            "stdout": "",
            "stderr": str(e),
        }


def run_trial(
    task_dir: Path,
    experiment_id: str,
    mode: str,
    builder: str,
    reviewer: str | None,
    trial_num: int,
    timeout: int = 300,
    eval_only: bool = False,
    workspace_override: Path | None = None,
) -> tuple[str, EvalResult]:
    """Run one complete trial: execute + evaluate + record.

    Returns (trial_id, EvalResult).
    """
    import yaml

    # Load metadata
    meta_path = task_dir / "metadata.yaml"
    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    task_name = metadata.get("id", task_dir.name)
    complexity = metadata.get("complexity", "medium")
    complexity_score = metadata.get("complexity_score")
    tags = metadata.get("tags", [])

    print(f"\n{'='*60}")
    print(f"  Trial #{trial_num}: {task_name} [{mode}]")
    print(f"  Complexity: {complexity} ({complexity_score})")
    print(f"  Builder: {builder} | Reviewer: {reviewer or 'N/A'}")
    print(f"{'='*60}")

    # Prepare workspace
    workspace = workspace_override or (BENCHMARK_DIR / "workspaces" / f"{task_name}-{mode}-{trial_num}")
    if not eval_only:
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        # Copy requirement to workspace
        shutil.copy2(task_dir / "REQUIREMENT.md", workspace / "REQUIREMENT.md")

    # Create trial in DB
    trial_id = create_trial(
        experiment_id=experiment_id,
        requirement=(task_dir / "REQUIREMENT.md").read_text(encoding="utf-8")[:500],
        agent_mode=mode,
        builder_agent=builder,
        reviewer_agent=reviewer if mode == "multi" else None,
        task_id=f"{task_name}-{mode}-{trial_num}",
        complexity=complexity,
        complexity_score=complexity_score,
        workflow_mode="normal",
        tags=tags + [mode],
    )

    # Run agent(s)
    agent_result = {"success": True, "elapsed_sec": 0}
    if not eval_only:
        agent_result = run_agent_on_task(
            task_dir, workspace, builder, reviewer, mode, timeout
        )
        print(f"  Agent finished: {'OK' if agent_result['success'] else 'FAIL'} "
              f"({agent_result['elapsed_sec']:.1f}s)")

    # Evaluate
    print("  Evaluating...")
    eval_result = evaluate_trial(task_dir, workspace, task_id=trial_id)
    print_report(eval_result)

    # Record to DB
    status = "approved" if eval_result.quality_score >= 50 else "failed"

    # Record builder run
    build_run_id = record_agent_run(
        trial_id=trial_id,
        agent_id=builder,
        role="builder",
        invocation_seq=1,
        duration_sec=agent_result["elapsed_sec"],
        status="completed" if agent_result["success"] else "error",
        output_summary=f"quality_score={eval_result.quality_score:.1f}",
    )

    # Record quality gates from evaluation
    for check_name, passed in eval_result.checks.items():
        record_quality_gate(
            run_id=build_run_id,
            trial_id=trial_id,
            check_name=check_name,
            passed=passed,
            details={"score": getattr(eval_result, f"{check_name}_score", None)
                      if hasattr(eval_result, f"{check_name}_score") else None},
        )

    # Record reviewer run if multi
    if mode == "multi" and reviewer:
        record_agent_run(
            trial_id=trial_id,
            agent_id=reviewer,
            role="reviewer",
            invocation_seq=2,
            status="completed",
        )

    # Complete trial
    complete_trial(
        trial_id,
        status=status,
        wall_clock_sec=agent_result["elapsed_sec"],
        build_time_sec=agent_result["elapsed_sec"] * 0.7 if mode == "single" else agent_result["elapsed_sec"] * 0.5,
        review_time_sec=0 if mode == "single" else agent_result["elapsed_sec"] * 0.3,
    )

    # Try to backfill finops
    backfill_finops(trial_id, f"{task_name}-{mode}-{trial_num}")

    return trial_id, eval_result


def run_experiment(
    name: str,
    hypothesis: str,
    tasks: list[Path],
    builder: str,
    reviewer: str | None,
    modes: list[str],
    reps: int = 1,
    timeout: int = 300,
) -> str:
    """Run a full experiment: multiple tasks x modes x replications."""

    init_db()
    experiment_id = create_experiment(
        name=name,
        hypothesis=hypothesis,
        config_snapshot={
            "builder": builder,
            "reviewer": reviewer,
            "modes": modes,
            "reps": reps,
            "timeout": timeout,
            "task_count": len(tasks),
        },
    )

    print(f"\n{'#'*60}")
    print(f"  Experiment: {name}")
    print(f"  ID: {experiment_id}")
    print(f"  Tasks: {len(tasks)} | Modes: {modes} | Reps: {reps}")
    print(f"  Total trials: {len(tasks) * len(modes) * reps}")
    print(f"{'#'*60}")

    results: list[tuple[str, str, str, float]] = []  # (task, mode, trial_id, score)

    for task_dir in tasks:
        for mode in modes:
            for rep in range(1, reps + 1):
                rev = reviewer if mode == "multi" else None
                trial_id, eval_result = run_trial(
                    task_dir=task_dir,
                    experiment_id=experiment_id,
                    mode=mode,
                    builder=builder,
                    reviewer=rev,
                    trial_num=rep,
                    timeout=timeout,
                )
                results.append((task_dir.name, mode, trial_id, eval_result.quality_score))

    # Summary
    print(f"\n{'#'*60}")
    print(f"  EXPERIMENT COMPLETE: {experiment_id}")
    print(f"{'#'*60}\n")
    print(f"  {'Task':<25} {'Mode':<8} {'Score':>6}")
    print(f"  {'─'*25} {'─'*8} {'─'*6}")
    for task, mode, _, score in results:
        print(f"  {task:<25} {mode:<8} {score:>5.1f}")

    # Mode averages
    for mode in modes:
        scores = [s for _, m, _, s in results if m == mode]
        if scores:
            avg = sum(scores) / len(scores)
            print(f"\n  [{mode}] Average: {avg:.1f}")

    print(f"\n  View full results: my bench status --experiment {experiment_id}")
    print(f"  Export: my bench export v_trial_summary -o results.csv\n")

    return experiment_id


def main():
    parser = argparse.ArgumentParser(description="Benchmark experiment runner")
    parser.add_argument("--task", help="Run single task (directory name)")
    parser.add_argument("--experiment", default="benchmark run", help="Experiment name")
    parser.add_argument("--hypothesis", default="", help="Research hypothesis")
    parser.add_argument("--mode", choices=["single", "multi", "both"], default="both")
    parser.add_argument("--builder", required=True, help="Builder agent ID")
    parser.add_argument("--reviewer", default=None, help="Reviewer agent ID (multi mode)")
    parser.add_argument("--reps", type=int, default=1, help="Replications per condition")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout per trial (seconds)")
    parser.add_argument("--eval-only", action="store_true", help="Only evaluate, don't run agents")
    parser.add_argument("--workspace", default=None, help="Workspace path (for --eval-only)")
    args = parser.parse_args()

    modes = {"both": ["single", "multi"], "single": ["single"], "multi": ["multi"]}[args.mode]

    if args.task:
        # Single task mode
        task_dir = TASKS_DIR / args.task
        if not task_dir.exists():
            print(f"Task not found: {task_dir}", file=sys.stderr)
            print(f"Available: {[t.name for t in discover_tasks()]}", file=sys.stderr)
            sys.exit(1)
        tasks = [task_dir]
    else:
        tasks = discover_tasks()

    if not tasks:
        print("No benchmark tasks found!", file=sys.stderr)
        sys.exit(1)

    run_experiment(
        name=args.experiment,
        hypothesis=args.hypothesis,
        tasks=tasks,
        builder=args.builder,
        reviewer=args.reviewer or args.builder,
        modes=modes,
        reps=args.reps,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
