#!/usr/bin/env python3
"""
MAST Failure Analysis — Extract and classify decomposition-induced failures.

Based on the MAST taxonomy (arXiv 2503.13657), adapted for SE task decomposition.
Identifies runs where decomposition caused failure (B/C fails while A passes),
extracts diagnostic artifacts, and generates a coding template for manual
failure classification.

Usage:
    # Extract all decomposition-induced failures
    python scripts/failure_analysis.py --extract

    # Generate coding template (CSV for manual classification)
    python scripts/failure_analysis.py --template

    # Compute inter-rater reliability after coding
    python scripts/failure_analysis.py --reliability --coder1 coding_1.csv --coder2 coding_2.csv

    # Summary statistics of failure modes
    python scripts/failure_analysis.py --summary --coded coded_failures.csv

Reference: experiment plan Phase 5 (MAST Failure Analysis)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "experiment_v3"
ANALYSIS_DIR = PROJECT_ROOT / "results" / "failure_analysis"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger(__name__)

# ── MAST-Adapted Failure Taxonomy ──────────────────────────
#
# Based on "Why Do Multi-Agent LLM Systems Fail?" (arXiv 2503.13657)
# Adapted for task decomposition in software engineering.

FAILURE_MODES = {
    # Category 1: Specification & Design Failures
    "SF1": "Incomplete sub-task specification (necessary context omitted from sub-task prompt)",
    "SF2": "Ambiguous interface contract (unclear API between sub-tasks)",
    "SF3": "Conflicting assumptions between sub-tasks",
    "SF4": "Over-decomposition (task split that shouldn't have been split)",
    "SF5": "Wrong decomposition boundary (split at wrong seam)",

    # Category 2: Communication & Coordination Failures
    "CF1": "Information loss at boundary (data/context not passed downstream)",
    "CF2": "Stale context (downstream uses outdated upstream state)",
    "CF3": "Missing shared state propagation (global state not synced)",
    "CF4": "Redundant/conflicting edits (sub-tasks modify same code differently)",

    # Category 3: Integration & Verification Failures
    "IF1": "Interface signature mismatch (function args/return types don't match)",
    "IF2": "Behavioral mismatch (correct interface, wrong semantics)",
    "IF3": "Side-effect conflict (sub-tasks have unintended interactions)",
    "IF4": "Incomplete integration (sub-tasks work individually but not together)",
    "IF5": "Premature termination (sub-task exits before completing all work)",
}

CATEGORIES = {
    "Specification": ["SF1", "SF2", "SF3", "SF4", "SF5"],
    "Communication": ["CF1", "CF2", "CF3", "CF4"],
    "Integration": ["IF1", "IF2", "IF3", "IF4", "IF5"],
}


def load_experiment_results() -> dict[str, list[dict]]:
    """Load all experiment results, grouped by condition."""
    results: dict[str, list[dict]] = defaultdict(list)

    for condition_dir in RESULTS_DIR.iterdir():
        if not condition_dir.is_dir():
            continue
        condition = condition_dir.name
        for task_dir in condition_dir.iterdir():
            if not task_dir.is_dir():
                continue
            for run_file in sorted(task_dir.glob("run_*.json")):
                try:
                    data = json.loads(run_file.read_text(encoding="utf-8"))
                    data["_file"] = str(run_file)
                    results[condition].append(data)
                except (json.JSONDecodeError, KeyError):
                    continue

    return dict(results)


def find_decomposition_induced_failures(
    results: dict[str, list[dict]],
) -> list[dict]:
    """Find cases where decomposition caused failure (B/C fails, A passes).

    A decomposition-induced failure is defined as:
    - Condition A (single) PASSES on (task_id, run_idx)
    - Condition B or C (decompose/decompose_bridge) FAILS on the same (task_id, run_idx)
    """
    # Index A results by (task_id, run_idx)
    a_results = {}
    for r in results.get("single", []):
        key = (r["task_id"], r["run_idx"])
        a_results[key] = r

    failures = []
    for condition in ["decompose", "decompose_bridge", "adaptive_bridge"]:
        for r in results.get(condition, []):
            key = (r["task_id"], r["run_idx"])
            a_result = a_results.get(key)
            if a_result is None:
                continue

            a_passed = a_result["metrics"]["resolve_rate"]
            b_passed = r["metrics"]["resolve_rate"]

            if a_passed and not b_passed:
                failures.append({
                    "task_id": r["task_id"],
                    "run_idx": r["run_idx"],
                    "condition": condition,
                    "condition_label": r.get("condition_label", condition),
                    "a_tests": f"{a_result['metrics']['tests_passed']}/{a_result['metrics']['tests_total']}",
                    "b_tests": f"{r['metrics']['tests_passed']}/{r['metrics']['tests_total']}",
                    "b_wall_sec": r["metrics"]["wall_clock_sec"],
                    "b_cost_usd": r["metrics"].get("cost_usd", 0),
                    "b_tokens": r["metrics"].get("total_tokens", 0),
                    "sub_task_count": r["metrics"].get("sub_task_count", 0),
                    "bridge_violations": r["metrics"].get("bridge_violations", 0),
                    "decompose_decision": r["metrics"].get("decompose_decision", ""),
                    "b_file": r.get("_file", ""),
                })

    # Sort by task_id, condition, run_idx
    failures.sort(key=lambda f: (f["task_id"], f["condition"], f["run_idx"]))
    return failures


def extract_diagnostic_artifacts(failures: list[dict]) -> None:
    """Extract diagnostic artifacts for each failure for manual analysis."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    for i, failure in enumerate(failures):
        task_id = failure["task_id"]
        condition = failure["condition"]
        run_idx = failure["run_idx"]
        artifact_dir = ANALYSIS_DIR / f"{task_id}_{condition}_run{run_idx}"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # Save failure summary
        summary_file = artifact_dir / "failure_summary.json"
        summary_file.write_text(
            json.dumps(failure, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Try to find decomposition plan
        decompose_cache = PROJECT_ROOT / ".multi-agent" / "cache"
        if decompose_cache.exists():
            for f in decompose_cache.glob("decompose-*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("task_id") == task_id:
                        (artifact_dir / "decomposition_plan.json").write_text(
                            json.dumps(data, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        # Try to find bridge contracts
        bridge_dir = PROJECT_ROOT / ".multi-agent" / "bridge_contracts"
        if bridge_dir.exists():
            for f in bridge_dir.glob(f"*{task_id}*"):
                import shutil
                shutil.copy2(f, artifact_dir / f.name)

        _log.info("[%d/%d] Extracted artifacts for %s/%s/run_%d → %s",
                  i + 1, len(failures), task_id, condition, run_idx, artifact_dir)


def generate_coding_template(failures: list[dict]) -> None:
    """Generate CSV template for manual MAST failure coding."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    template_file = ANALYSIS_DIR / "coding_template.csv"

    fieldnames = [
        "failure_id", "task_id", "condition", "run_idx",
        "a_tests", "b_tests", "sub_task_count",
        "primary_failure_mode", "secondary_failure_mode",
        "confidence", "notes",
    ]

    with open(template_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, failure in enumerate(failures):
            writer.writerow({
                "failure_id": f"F{i+1:03d}",
                "task_id": failure["task_id"],
                "condition": failure["condition"],
                "run_idx": failure["run_idx"],
                "a_tests": failure["a_tests"],
                "b_tests": failure["b_tests"],
                "sub_task_count": failure["sub_task_count"],
                "primary_failure_mode": "",  # To be filled by coder
                "secondary_failure_mode": "",  # Optional
                "confidence": "",  # high/medium/low
                "notes": "",
            })

    print(f"\nCoding template saved to: {template_file}")
    print(f"Total failures to code: {len(failures)}")
    print(f"\nFailure mode codes:")
    for code, desc in FAILURE_MODES.items():
        print(f"  {code}: {desc}")


def compute_inter_rater_reliability(
    coder1_file: str, coder2_file: str,
) -> None:
    """Compute Cohen's kappa for inter-rater reliability."""
    codes1 = _load_coded_csv(coder1_file)
    codes2 = _load_coded_csv(coder2_file)

    # Match by failure_id
    common_ids = set(codes1.keys()) & set(codes2.keys())
    if not common_ids:
        print("No matching failure IDs between the two codings.")
        return

    agreements = 0
    total = len(common_ids)
    labels_1 = []
    labels_2 = []

    for fid in sorted(common_ids):
        c1 = codes1[fid]
        c2 = codes2[fid]
        labels_1.append(c1)
        labels_2.append(c2)
        if c1 == c2:
            agreements += 1

    # Raw agreement
    p_o = agreements / total

    # Expected agreement (by chance)
    all_labels = set(labels_1 + labels_2)
    p_e = 0
    for label in all_labels:
        p1 = labels_1.count(label) / total
        p2 = labels_2.count(label) / total
        p_e += p1 * p2

    # Cohen's kappa
    kappa = (p_o - p_e) / (1 - p_e) if p_e < 1 else 1.0

    print(f"\nInter-Rater Reliability:")
    print(f"  Total coded: {total}")
    print(f"  Agreements: {agreements} ({p_o:.1%})")
    print(f"  Cohen's kappa: {kappa:.3f}")
    print(f"  Interpretation: ", end="")
    if kappa >= 0.81:
        print("Almost perfect agreement")
    elif kappa >= 0.61:
        print("Substantial agreement")
    elif kappa >= 0.41:
        print("Moderate agreement")
    elif kappa >= 0.21:
        print("Fair agreement")
    else:
        print("Slight agreement (may need recoding)")

    # Disagreements
    print(f"\n  Disagreements:")
    for fid in sorted(common_ids):
        if codes1[fid] != codes2[fid]:
            print(f"    {fid}: coder1={codes1[fid]}, coder2={codes2[fid]}")


def print_summary(coded_file: str) -> None:
    """Print failure mode frequency distribution from coded CSV."""
    codes = _load_coded_csv(coded_file)

    print(f"\n{'='*70}")
    print(f"  MAST Failure Analysis Summary ({len(codes)} failures)")
    print(f"{'='*70}")

    # Count by primary failure mode
    mode_counts = Counter(codes.values())

    # By category
    for cat_name, cat_codes in CATEGORIES.items():
        cat_total = sum(mode_counts.get(c, 0) for c in cat_codes)
        pct = cat_total / len(codes) * 100 if codes else 0
        print(f"\n  {cat_name} ({cat_total}, {pct:.1f}%):")
        for code in cat_codes:
            count = mode_counts.get(code, 0)
            if count > 0:
                bar = "█" * count
                desc = FAILURE_MODES[code][:60]
                print(f"    {code}: {count:>3} {bar} — {desc}")

    # Top failure modes
    print(f"\n  Top 5 failure modes:")
    for code, count in mode_counts.most_common(5):
        pct = count / len(codes) * 100
        print(f"    {code}: {count} ({pct:.1f}%) — {FAILURE_MODES.get(code, '?')}")


def _load_coded_csv(filepath: str) -> dict[str, str]:
    """Load failure coding from CSV. Returns {failure_id: primary_failure_mode}."""
    codes = {}
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fid = row.get("failure_id", "")
            mode = row.get("primary_failure_mode", "").strip()
            if fid and mode:
                codes[fid] = mode
    return codes


def main() -> None:
    parser = argparse.ArgumentParser(description="MAST Failure Analysis for decomposition experiments")
    parser.add_argument("--extract", action="store_true", help="Extract decomposition-induced failures")
    parser.add_argument("--template", action="store_true", help="Generate coding template CSV")
    parser.add_argument("--reliability", action="store_true", help="Compute inter-rater reliability")
    parser.add_argument("--summary", action="store_true", help="Print failure mode summary")
    parser.add_argument("--coder1", help="Path to coder 1 CSV (for --reliability)")
    parser.add_argument("--coder2", help="Path to coder 2 CSV (for --reliability)")
    parser.add_argument("--coded", help="Path to coded CSV (for --summary)")
    parser.add_argument("--results-dir", help="Override results directory")
    args = parser.parse_args()

    global RESULTS_DIR
    if args.results_dir:
        RESULTS_DIR = Path(args.results_dir)

    if args.extract or args.template:
        _log.info("Loading experiment results from %s...", RESULTS_DIR)
        results = load_experiment_results()
        if not results:
            # Fallback: try v2 results
            RESULTS_DIR = PROJECT_ROOT / "results" / "experiment_v2"
            results = load_experiment_results()

        failures = find_decomposition_induced_failures(results)
        print(f"\nFound {len(failures)} decomposition-induced failures")

        if args.extract:
            extract_diagnostic_artifacts(failures)

        if args.template:
            generate_coding_template(failures)

        # Print summary table
        by_condition = Counter(f["condition"] for f in failures)
        by_task = Counter(f["task_id"] for f in failures)
        print(f"\n  By condition: {dict(by_condition)}")
        print(f"  By task: {dict(by_task)}")

    elif args.reliability:
        if not args.coder1 or not args.coder2:
            print("Error: --reliability requires --coder1 and --coder2")
            return
        compute_inter_rater_reliability(args.coder1, args.coder2)

    elif args.summary:
        if not args.coded:
            print("Error: --summary requires --coded")
            return
        print_summary(args.coded)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
