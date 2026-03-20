#!/usr/bin/env python3
"""
SWE-bench Batch Runner — orchestrate SWE-bench experiments across conditions.

Usage:
    # One-time setup: clone all sampled repos
    python scripts/run_swebench_batch.py --setup-only --sample 30

    # Run single condition
    python scripts/run_swebench_batch.py --condition single --runs 3

    # Run all 4 conditions (single, multi, adaptive, adaptive_bridge)
    python scripts/run_swebench_batch.py --full --runs 3

    # Analyze results
    python scripts/run_swebench_batch.py --analyze

Reference: experiment-protocol-v2.md §4
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Default conditions for SWE-bench experiments (skip fixed variants to save cost)
SWEBENCH_CONDITIONS = ["single", "multi", "adaptive", "adaptive_bridge"]
ALL_CONDITIONS = [
    "single", "multi", "fixed_decompose", "fixed_bridge",
    "adaptive", "adaptive_bridge",
]

DEFAULT_SAMPLE = 30
DEFAULT_RUNS = 3
DEFAULT_SEED = 42
DEFAULT_SPLIT = "verified"


def run_setup(sample: int, seed: int, split: str) -> bool:
    """Clone all sampled SWE-bench instances."""
    print(f"\n{'='*60}")
    print(f"  Setting up {sample} SWE-bench instances (split={split}, seed={seed})")
    print(f"{'='*60}\n")

    cmd = [
        sys.executable, str(SCRIPT_DIR / "swebench_adapter.py"),
        "--split", split,
        "--sample", str(sample),
        "--seed", str(seed),
        "--setup-all",
    ]
    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return r.returncode == 0


def run_condition(
    condition: str,
    runs: int,
    sample: int,
    seed: int,
    split: str,
    *,
    builder: str = "claude",
    dry_run: bool = False,
) -> bool:
    """Run a single experimental condition on SWE-bench tasks."""
    print(f"\n{'='*60}")
    print(f"  Running condition: {condition} ({runs} reps, {sample} tasks)")
    print(f"{'='*60}\n")

    cmd = [
        sys.executable, str(SCRIPT_DIR / "experiment_runner_v2.py"),
        "--swebench",
        "--swebench-sample", str(sample),
        "--swebench-seed", str(seed),
        "--swebench-split", split,
        "--condition", condition,
        "--runs", str(runs),
        "--builder", builder,
    ]
    if dry_run:
        cmd.append("--dry-run")

    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return r.returncode == 0


def run_analysis(split: str) -> None:
    """Analyze SWE-bench experiment results."""
    cmd = [
        sys.executable, str(SCRIPT_DIR / "experiment_runner_v2.py"),
        "--swebench",
        "--swebench-split", split,
        "--analyze",
    ]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT))


def show_progress(results_dir: Path) -> None:
    """Show progress across all conditions."""
    if not results_dir.exists():
        print("No results yet.")
        return

    print(f"\n{'='*60}")
    print(f"  SWE-bench Progress ({results_dir})")
    print(f"{'='*60}\n")

    for cond_dir in sorted(results_dir.iterdir()):
        if not cond_dir.is_dir():
            continue
        total, passed = 0, 0
        for json_file in cond_dir.rglob("run_*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                total += 1
                if data.get("metrics", {}).get("resolve_rate"):
                    passed += 1
            except Exception:
                total += 1
        rate = f"{passed}/{total}" if total else "0/0"
        pct = f"({passed/total:.0%})" if total else ""
        print(f"  {cond_dir.name:<25} {rate:>8} {pct}")

    print()


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Batch Runner")
    parser.add_argument("--setup-only", action="store_true",
                        help="Only clone repos, don't run experiments")
    parser.add_argument("--condition", choices=ALL_CONDITIONS,
                        help="Run a single condition")
    parser.add_argument("--full", action="store_true",
                        help="Run all default conditions")
    parser.add_argument("--all-conditions", action="store_true",
                        help="Run all 6 conditions (including fixed variants)")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--progress", action="store_true",
                        help="Show current progress")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--split", default=DEFAULT_SPLIT,
                        choices=["verified", "lite"])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--builder", default="claude")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results_dir = PROJECT_ROOT / "results" / "swebench_v1"

    if args.progress:
        show_progress(results_dir)
        return

    if args.analyze:
        run_analysis(args.split)
        return

    if args.setup_only:
        ok = run_setup(args.sample, args.seed, args.split)
        sys.exit(0 if ok else 1)

    # Determine conditions to run
    if args.condition:
        conditions = [args.condition]
    elif args.all_conditions:
        conditions = ALL_CONDITIONS
    elif args.full:
        conditions = SWEBENCH_CONDITIONS
    else:
        parser.print_help()
        print("\nSpecify --condition, --full, --all-conditions, --setup-only, or --analyze")
        sys.exit(1)

    # Setup first (idempotent — skips already-cloned repos)
    print("Ensuring instances are set up...")
    run_setup(args.sample, args.seed, args.split)

    # Run each condition
    t0 = time.time()
    results = {}
    for cond in conditions:
        ok = run_condition(
            cond, args.runs, args.sample, args.seed, args.split,
            builder=args.builder, dry_run=args.dry_run,
        )
        results[cond] = "OK" if ok else "FAILED"

    elapsed = time.time() - t0
    hours, rem = divmod(int(elapsed), 3600)
    mins, secs = divmod(rem, 60)

    print(f"\n{'='*60}")
    print(f"  Batch complete in {hours}h {mins}m {secs}s")
    print(f"{'='*60}")
    for cond, status in results.items():
        print(f"  {cond:<25} {status}")

    if not args.dry_run:
        print(f"\nAnalyze: python scripts/run_swebench_batch.py --analyze")
        show_progress(results_dir)


if __name__ == "__main__":
    main()
