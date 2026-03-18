#!/usr/bin/env python3
"""
Statistical analysis and visualization for experiment-protocol-v2.

Generates:
1. Summary tables (Markdown + LaTeX)
2. Pairwise statistical comparisons
3. Effect sizes (Cliff's delta, Odds Ratio)
4. Stratified analysis by complexity
5. Cost-effectiveness analysis
6. Figures (bar charts, Pareto frontiers)

Usage:
    python scripts/analyze_experiment.py
    python scripts/analyze_experiment.py --results-dir results/experiment_v2
    python scripts/analyze_experiment.py --latex    # LaTeX table output
    python scripts/analyze_experiment.py --figures  # Generate matplotlib figures
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results" / "experiment_v2"
FIGURES_DIR = PROJECT_ROOT / "results" / "experiment_v2" / "figures"

CONDITION_ORDER = [
    "single", "multi", "fixed_decompose",
    "fixed_bridge", "adaptive", "adaptive_bridge",
]
CONDITION_LABELS = {
    "single": "C1: Single",
    "multi": "C2: Multi",
    "fixed_decompose": "C3: FixedDecomp",
    "fixed_bridge": "C4: FixedDecomp+Bridge",
    "adaptive": "C5: Adaptive",
    "adaptive_bridge": "C6: Adaptive+Bridge",
}
COMPLEXITY_ORDER = ["simple", "medium", "complex"]


# ── Data Loading ─────────────────────────────────────────


def load_all_results(results_dir: Path) -> dict[str, list[dict]]:
    """Load all result JSON files, grouped by condition."""
    results: dict[str, list[dict]] = {}
    for json_file in sorted(results_dir.rglob("run_*.json")):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        cond = data.get("condition", "unknown")
        results.setdefault(cond, []).append(data)
    return results


def get_metrics(runs: list[dict]) -> list[dict]:
    return [r.get("metrics", {}) for r in runs]


# ── Statistical Tests ────────────────────────────────────


def cliffs_delta(x: list[float], y: list[float]) -> tuple[float, str]:
    """Compute Cliff's delta effect size."""
    n_x, n_y = len(x), len(y)
    if n_x == 0 or n_y == 0:
        return 0.0, "negligible"
    gt = sum(1 for a in x for b in y if a > b)
    lt = sum(1 for a in x for b in y if a < b)
    delta = (gt - lt) / (n_x * n_y)
    if abs(delta) < 0.147:
        mag = "negligible"
    elif abs(delta) < 0.33:
        mag = "small"
    elif abs(delta) < 0.474:
        mag = "medium"
    else:
        mag = "large"
    return delta, mag


def pairwise_comparison(
    results: dict[str, list[dict]],
    cond_a: str,
    cond_b: str,
    metric: str = "resolve_rate",
) -> dict[str, Any]:
    """Compare two conditions on a metric."""
    runs_a = results.get(cond_a, [])
    runs_b = results.get(cond_b, [])
    if len(runs_a) < 2 or len(runs_b) < 2:
        return {"error": "insufficient data", "n_a": len(runs_a), "n_b": len(runs_b)}

    m_a = get_metrics(runs_a)
    m_b = get_metrics(runs_b)

    if metric == "resolve_rate":
        scores_a = [1.0 if m.get("resolve_rate") else 0.0 for m in m_a]
        scores_b = [1.0 if m.get("resolve_rate") else 0.0 for m in m_b]
    else:
        scores_a = [m.get(metric, 0) for m in m_a]
        scores_b = [m.get(metric, 0) for m in m_b]

    result: dict[str, Any] = {
        "cond_a": cond_a, "cond_b": cond_b, "metric": metric,
        "n_a": len(scores_a), "n_b": len(scores_b),
        "mean_a": sum(scores_a) / len(scores_a) if scores_a else 0,
        "mean_b": sum(scores_b) / len(scores_b) if scores_b else 0,
    }

    # Cliff's delta
    delta, mag = cliffs_delta(scores_a, scores_b)
    result["cliffs_delta"] = delta
    result["effect_magnitude"] = mag

    # Mann-Whitney U
    try:
        from scipy.stats import mannwhitneyu
        stat, p = mannwhitneyu(scores_a, scores_b, alternative="two-sided")
        result["U"] = stat
        result["p_value"] = p
        result["significant_005"] = p < 0.05
        result["significant_holm"] = p < 0.0083  # Holm-Bonferroni for 6 comparisons
    except (ImportError, ValueError) as e:
        result["test_error"] = str(e)

    return result


# ── Summary Tables ───────────────────────────────────────


def summary_table_markdown(results: dict[str, list[dict]]) -> str:
    """Generate Markdown summary table."""
    lines = [
        "## Summary Results",
        "",
        "| Condition | N | Resolve% | TestPass% | Avg Duration | Avg Retry |",
        "|-----------|---|----------|-----------|-------------|-----------|",
    ]

    for cond in CONDITION_ORDER:
        runs = results.get(cond, [])
        if not runs:
            continue
        n = len(runs)
        m = get_metrics(runs)
        resolve = sum(1 for x in m if x.get("resolve_rate")) / n
        test_pass = sum(x.get("test_pass_rate", 0) for x in m) / n
        avg_dur = sum(x.get("wall_clock_sec", 0) for x in m) / n
        avg_retry = sum(x.get("retry_count", 0) for x in m) / n
        label = CONDITION_LABELS.get(cond, cond)
        lines.append(
            f"| {label} | {n} | {resolve:.0%} | {test_pass:.1%} | "
            f"{avg_dur:.0f}s | {avg_retry:.1f} |"
        )

    return "\n".join(lines)


def summary_table_latex(results: dict[str, list[dict]]) -> str:
    """Generate LaTeX table for paper."""
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Experiment results across conditions (N per cell shown).}",
        r"\label{tab:results}",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Condition & N & Resolve\% & Avg Duration (s) & Avg Retry \\",
        r"\midrule",
    ]

    for cond in CONDITION_ORDER:
        runs = results.get(cond, [])
        if not runs:
            continue
        n = len(runs)
        m = get_metrics(runs)
        resolve = sum(1 for x in m if x.get("resolve_rate")) / n * 100
        avg_dur = sum(x.get("wall_clock_sec", 0) for x in m) / n
        avg_retry = sum(x.get("retry_count", 0) for x in m) / n
        label = CONDITION_LABELS.get(cond, cond).replace("&", r"\&")
        lines.append(f"{label} & {n} & {resolve:.1f} & {avg_dur:.0f} & {avg_retry:.1f} \\\\")

    lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])
    return "\n".join(lines)


def stratified_table_markdown(results: dict[str, list[dict]]) -> str:
    """Generate complexity-stratified table."""
    lines = [
        "## Stratified Results by Task Complexity",
        "",
    ]

    for complexity in COMPLEXITY_ORDER:
        lines.append(f"### {complexity.title()} Tasks")
        lines.append("")
        lines.append("| Condition | N | Resolve% | Avg Duration |")
        lines.append("|-----------|---|----------|-------------|")

        for cond in CONDITION_ORDER:
            runs = [
                r for r in results.get(cond, [])
                if r.get("task_complexity") == complexity
            ]
            if not runs:
                continue
            n = len(runs)
            m = get_metrics(runs)
            resolve = sum(1 for x in m if x.get("resolve_rate")) / n
            avg_dur = sum(x.get("wall_clock_sec", 0) for x in m) / n
            label = CONDITION_LABELS.get(cond, cond)
            lines.append(f"| {label} | {n} | {resolve:.0%} | {avg_dur:.0f}s |")

        lines.append("")

    return "\n".join(lines)


def comparisons_table(results: dict[str, list[dict]]) -> str:
    """Generate pairwise comparison results."""
    comparisons = [
        ("adaptive_bridge", "fixed_decompose", "C6 vs C3 (primary claim)"),
        ("adaptive_bridge", "multi", "C6 vs C2 (no degradation)"),
        ("adaptive", "fixed_decompose", "C5 vs C3 (adaptive ablation)"),
        ("fixed_bridge", "fixed_decompose", "C4 vs C3 (bridge ablation)"),
        ("multi", "single", "C2 vs C1 (multi-agent value)"),
        ("adaptive_bridge", "single", "C6 vs C1 (full system vs baseline)"),
    ]

    lines = [
        "## Pairwise Comparisons",
        "",
        "| Comparison | Mean A | Mean B | Cliff's δ | Effect | U | p-value | Sig |",
        "|-----------|--------|--------|-----------|--------|---|---------|-----|",
    ]

    for cond_a, cond_b, label in comparisons:
        comp = pairwise_comparison(results, cond_a, cond_b, "resolve_rate")
        if "error" in comp:
            lines.append(f"| {label} | - | - | - | - | - | - | {comp['error']} |")
            continue

        p_str = f"{comp.get('p_value', '-'):.4f}" if "p_value" in comp else "-"
        sig = ""
        if comp.get("significant_holm"):
            sig = "***"
        elif comp.get("significant_005"):
            sig = "**"
        else:
            sig = "ns"

        lines.append(
            f"| {label} | {comp['mean_a']:.2f} | {comp['mean_b']:.2f} | "
            f"{comp.get('cliffs_delta', 0):+.3f} | {comp.get('effect_magnitude', '-')} | "
            f"{comp.get('U', '-')} | {p_str} | {sig} |"
        )

    lines.append("")
    lines.append("Significance: *** p<0.0083 (Holm-Bonferroni), ** p<0.05, ns = not significant")
    return "\n".join(lines)


def per_task_table(results: dict[str, list[dict]]) -> str:
    """Generate per-task breakdown table."""
    # Collect all task IDs
    all_tasks = set()
    for runs in results.values():
        for r in runs:
            all_tasks.add(r.get("task_id", ""))

    lines = [
        "## Per-Task Results",
        "",
        "| Task | Complexity | " + " | ".join(
            CONDITION_LABELS.get(c, c) for c in CONDITION_ORDER if c in results
        ) + " |",
        "|------|-----------|" + "|".join(
            "---" for c in CONDITION_ORDER if c in results
        ) + "|",
    ]

    for task_id in sorted(all_tasks):
        complexity = "?"
        cells = []
        for cond in CONDITION_ORDER:
            if cond not in results:
                continue
            task_runs = [r for r in results[cond] if r.get("task_id") == task_id]
            if not task_runs:
                cells.append("-")
                continue
            complexity = task_runs[0].get("task_complexity", "?")
            m = get_metrics(task_runs)
            passed = sum(1 for x in m if x.get("resolve_rate"))
            total = len(m)
            tests_detail = f"{m[0].get('tests_passed', 0)}/{m[0].get('tests_total', 0)}" if total == 1 else f"{passed}/{total}"
            cells.append(tests_detail)

        lines.append(f"| {task_id} | {complexity} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


# ── Figures ──────────────────────────────────────────────


def generate_figures(results: dict[str, list[dict]]) -> None:
    """Generate matplotlib figures for the paper."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib not installed. Install: pip install matplotlib")
        return

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    # Figure 1: Resolve rate by condition
    conditions = []
    resolve_rates = []
    for cond in CONDITION_ORDER:
        runs = results.get(cond, [])
        if not runs:
            continue
        m = get_metrics(runs)
        rate = sum(1 for x in m if x.get("resolve_rate")) / len(m)
        conditions.append(CONDITION_LABELS.get(cond, cond).replace("C", "\nC"))
        resolve_rates.append(rate * 100)

    if conditions:
        fig, ax = plt.subplots(figsize=(10, 5))
        colors = ["#4CAF50" if r == 100 else "#FF9800" if r >= 80 else "#F44336" for r in resolve_rates]
        bars = ax.bar(conditions, resolve_rates, color=colors, edgecolor="black", linewidth=0.5)
        ax.set_ylabel("Resolve Rate (%)")
        ax.set_title("Functional Correctness by Condition")
        ax.set_ylim(0, 110)
        for bar, rate in zip(bars, resolve_rates):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                    f"{rate:.0f}%", ha="center", va="bottom", fontsize=9)
        plt.tight_layout()
        fig.savefig(FIGURES_DIR / "resolve_rate_by_condition.png", dpi=150)
        plt.close()
        print(f"  Saved: {FIGURES_DIR / 'resolve_rate_by_condition.png'}")

    # Figure 2: Duration by condition
    durations = []
    cond_labels = []
    for cond in CONDITION_ORDER:
        runs = results.get(cond, [])
        if not runs:
            continue
        m = get_metrics(runs)
        dur = [x.get("wall_clock_sec", 0) for x in m]
        durations.append(dur)
        cond_labels.append(CONDITION_LABELS.get(cond, cond).replace("C", "\nC"))

    if durations:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.boxplot(durations, tick_labels=cond_labels)
        ax.set_ylabel("Duration (seconds)")
        ax.set_title("Wall-clock Duration by Condition")
        plt.tight_layout()
        fig.savefig(FIGURES_DIR / "duration_by_condition.png", dpi=150)
        plt.close()
        print(f"  Saved: {FIGURES_DIR / 'duration_by_condition.png'}")

    # Figure 3: Stratified resolve rate
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True)
    for ax_idx, complexity in enumerate(COMPLEXITY_ORDER):
        ax = axes[ax_idx]
        conds = []
        rates = []
        for cond in CONDITION_ORDER:
            runs = [r for r in results.get(cond, []) if r.get("task_complexity") == complexity]
            if not runs:
                continue
            m = get_metrics(runs)
            rate = sum(1 for x in m if x.get("resolve_rate")) / len(m) * 100
            conds.append(CONDITION_LABELS.get(cond, cond).split(":")[0])
            rates.append(rate)

        if conds:
            colors = ["#4CAF50" if r == 100 else "#FF9800" if r >= 80 else "#F44336" for r in rates]
            ax.bar(conds, rates, color=colors, edgecolor="black", linewidth=0.5)
        ax.set_title(f"{complexity.title()} Tasks")
        ax.set_ylim(0, 110)
        if ax_idx == 0:
            ax.set_ylabel("Resolve Rate (%)")

    plt.suptitle("Resolve Rate Stratified by Task Complexity")
    plt.tight_layout()
    fig.savefig(FIGURES_DIR / "stratified_resolve_rate.png", dpi=150)
    plt.close()
    print(f"  Saved: {FIGURES_DIR / 'stratified_resolve_rate.png'}")


# ── Main Report ──────────────────────────────────────────


def generate_report(results_dir: Path, *, latex: bool = False, figures: bool = False) -> None:
    """Generate full analysis report."""
    results = load_all_results(results_dir)
    if not results:
        print(f"No results found in {results_dir}")
        return

    total_runs = sum(len(v) for v in results.values())
    conditions_found = len(results)
    tasks_found = len(set(r.get("task_id", "") for runs in results.values() for r in runs))

    print(f"\n{'='*80}")
    print(f"  Experiment Analysis Report (Protocol v2)")
    print(f"  {total_runs} runs | {conditions_found} conditions | {tasks_found} tasks")
    print(f"{'='*80}\n")

    # Summary
    print(summary_table_markdown(results))
    print()

    # Stratified
    print(stratified_table_markdown(results))

    # Per-task
    print(per_task_table(results))
    print()

    # Comparisons
    print(comparisons_table(results))
    print()

    # LaTeX
    if latex:
        print("\n## LaTeX Table\n")
        print("```latex")
        print(summary_table_latex(results))
        print("```")

    # Figures
    if figures:
        print("\nGenerating figures...")
        generate_figures(results)

    # Save report
    report_path = results_dir / "analysis_report.md"
    report_lines = [
        f"# Experiment Analysis Report",
        f"",
        f"Generated from {total_runs} runs across {conditions_found} conditions and {tasks_found} tasks.",
        f"",
        summary_table_markdown(results),
        "",
        stratified_table_markdown(results),
        per_task_table(results),
        "",
        comparisons_table(results),
    ]
    if latex:
        report_lines.extend(["", "## LaTeX", "", "```latex", summary_table_latex(results), "```"])

    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nReport saved: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="Experiment analysis (protocol v2)")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--latex", action="store_true", help="Include LaTeX tables")
    parser.add_argument("--figures", action="store_true", help="Generate matplotlib figures")
    args = parser.parse_args()

    generate_report(args.results_dir, latex=args.latex, figures=args.figures)


if __name__ == "__main__":
    main()
