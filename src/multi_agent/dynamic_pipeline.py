"""Dynamic Pipeline Selection — per-sub-task pipeline optimization.

Classifies each decomposed sub-task by type and assigns it an optimized
role pipeline, rather than using a one-size-fits-all build-review cycle.

Inspired by:
- MASAI (arXiv 2024): modular sub-agents with per-task strategy tuning
- Self-Organized Agents (arXiv 2024): dynamic agent multiplication
- CodeR (arXiv 2024): pre-defined task graphs with plan selection

Novel contribution: per-sub-task pipeline selection in a decomposed
multi-agent workflow, where each sub-task gets the minimum viable
pipeline based on its classification.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_log = logging.getLogger(__name__)


# ── Sub-Task Type Classification ────────────────────────────


class SubTaskType(Enum):
    """Classification of a decomposed sub-task by its intent."""

    BUGFIX = "bugfix"
    NEW_FEATURE = "new_feature"
    REFACTOR = "refactor"
    TEST_ADDITION = "test_addition"
    CONFIG_CHANGE = "config_change"
    DOCUMENTATION = "documentation"
    API_ENDPOINT = "api_endpoint"
    DATA_MODEL = "data_model"
    INTEGRATION = "integration"


@dataclass
class SubTaskClassification:
    """Result of classifying a sub-task.

    Attributes:
        task_type: The determined sub-task type.
        confidence: Classification confidence in ``[0.0, 1.0]``.
        reasoning: Human-readable explanation of why this type was chosen.
        suggested_pipeline: The pipeline name recommended for this task type.
    """

    task_type: SubTaskType
    confidence: float
    reasoning: str
    suggested_pipeline: str


# ── Signal Definitions ──────────────────────────────────────

# Each entry maps SubTaskType -> (keywords, weight).
# Keywords are matched case-insensitively against the combined text of
# description + done_criteria + dependency names.

_SIGNAL_TABLE: list[tuple[SubTaskType, list[str], float]] = [
    (SubTaskType.BUGFIX, [
        "fix", "bug", "patch", "repair", "error", "crash", "broken",
        "regression", "hotfix", "issue", "defect", "fault",
    ], 1.0),
    (SubTaskType.TEST_ADDITION, [
        "test", "spec", "coverage", "assert", "unittest", "pytest",
        "test case", "test suite", "testing",
    ], 1.0),
    (SubTaskType.CONFIG_CHANGE, [
        "config", "env", "setting", "yaml", "toml", "json config",
        "environment", "configuration", ".env", "dotenv", "ini",
    ], 0.9),
    (SubTaskType.DOCUMENTATION, [
        "doc", "readme", "documentation", "docstring", "comment",
        "changelog", "wiki", "guide", "tutorial",
    ], 0.9),
    (SubTaskType.API_ENDPOINT, [
        "endpoint", "route", "api", "rest", "handler", "controller",
        "http", "request", "response", "middleware", "graphql",
    ], 0.85),
    (SubTaskType.DATA_MODEL, [
        "model", "schema", "migration", "table", "database", "orm",
        "entity", "field", "column", "relation",
    ], 0.85),
    (SubTaskType.REFACTOR, [
        "refactor", "rename", "restructure", "cleanup", "clean up",
        "reorganize", "simplify", "extract", "dedup", "deduplicate",
    ], 0.8),
    (SubTaskType.INTEGRATION, [
        "integrate", "integration", "connect", "bridge", "adapter",
        "plugin", "extension", "hook", "webhook", "third-party",
    ], 0.8),
]

# Compiled regex patterns for each signal group (case-insensitive word boundary)
_SIGNAL_PATTERNS: list[tuple[SubTaskType, re.Pattern[str], float]] = [
    (
        task_type,
        re.compile(
            r"\b(?:" + "|".join(re.escape(kw) for kw in keywords) + r")\b",
            re.IGNORECASE,
        ),
        weight,
    )
    for task_type, keywords, weight in _SIGNAL_TABLE
]


# ── Classification Logic ───────────────────────────────────


def classify_subtask(
    description: str,
    done_criteria: str = "",
    deps: list[str] | None = None,
) -> SubTaskClassification:
    """Classify a sub-task based on textual signals in its description.

    Scans the combined text of *description*, *done_criteria*, and
    dependency names for keyword signals and returns the best-matching
    ``SubTaskClassification``.

    Args:
        description: The sub-task description / title.
        done_criteria: Acceptance criteria or done-definition text.
        deps: Names or IDs of dependency sub-tasks (used as extra signal).

    Returns:
        A ``SubTaskClassification`` with the best matching type.
    """
    combined = " ".join(filter(None, [
        description,
        done_criteria,
        " ".join(deps) if deps else "",
    ]))

    if not combined.strip():
        return SubTaskClassification(
            task_type=SubTaskType.NEW_FEATURE,
            confidence=0.1,
            reasoning="Empty description; defaulting to NEW_FEATURE.",
            suggested_pipeline="standard",
        )

    # Score each type by counting keyword matches * weight
    scores: dict[SubTaskType, tuple[float, list[str]]] = {}

    for task_type, pattern, weight in _SIGNAL_PATTERNS:
        matches = pattern.findall(combined)
        if matches:
            score = len(matches) * weight
            scores[task_type] = (score, [m.lower() for m in matches])

    if not scores:
        return SubTaskClassification(
            task_type=SubTaskType.NEW_FEATURE,
            confidence=0.3,
            reasoning="No keyword signals matched; defaulting to NEW_FEATURE.",
            suggested_pipeline="standard",
        )

    # Pick the highest-scoring type
    best_type = max(scores, key=lambda t: scores[t][0])
    best_score, matched_keywords = scores[best_type]

    # Confidence: normalize by total score across all matches
    total_score = sum(s for s, _ in scores.values())
    confidence = round(min(best_score / max(total_score, 1.0), 1.0), 2)

    # Deduplicate keywords for readability
    unique_keywords = list(dict.fromkeys(matched_keywords))

    reasoning = (
        f"Matched {best_type.value} signals: {', '.join(unique_keywords)} "
        f"(score {best_score:.1f}/{total_score:.1f})."
    )

    return SubTaskClassification(
        task_type=best_type,
        confidence=confidence,
        reasoning=reasoning,
        suggested_pipeline="",  # filled by select_pipeline_for_subtask
    )


# ── Pipeline Selection ──────────────────────────────────────

# Pipeline tiers (lightest to heaviest):
#   "minimal"  — builder only, no review (fast, low-risk tasks)
#   "standard" — builder + reviewer (default for new features)
#   "verified" — builder + test-verify + reviewer (needs regression check)
#   "full"     — architect + builder + test-verify + reviewer (complex integration)


def select_pipeline_for_subtask(
    classification: SubTaskClassification,
    parent_complexity: str = "medium",
) -> str:
    """Select the optimal pipeline for a classified sub-task.

    Args:
        classification: The sub-task classification result.
        parent_complexity: Complexity of the parent task
            (``"simple"``, ``"medium"``, ``"complex"``).

    Returns:
        Pipeline name: ``"minimal"``, ``"standard"``, ``"verified"``,
        or ``"full"``.
    """
    task_type = classification.task_type

    # Minimal pipeline: low-risk, well-scoped tasks
    if task_type in (
        SubTaskType.BUGFIX,
        SubTaskType.TEST_ADDITION,
        SubTaskType.CONFIG_CHANGE,
        SubTaskType.DOCUMENTATION,
    ):
        return "minimal"

    # Verified pipeline: needs regression/test verification
    if task_type in (
        SubTaskType.API_ENDPOINT,
        SubTaskType.DATA_MODEL,
        SubTaskType.REFACTOR,
    ):
        return "verified"

    # Full pipeline: complex integration work
    if task_type == SubTaskType.INTEGRATION:
        return "full"

    # NEW_FEATURE: depends on parent complexity
    if task_type == SubTaskType.NEW_FEATURE:
        if parent_complexity == "simple":
            return "standard"
        if parent_complexity == "complex":
            return "verified"
        return "standard"  # medium

    # Fallback
    return "standard"


# ── Sub-Task Enrichment ─────────────────────────────────────


def enrich_subtasks(
    sub_tasks: list[dict[str, Any]],
    parent_complexity: str = "medium",
) -> list[dict[str, Any]]:
    """Classify each sub-task and return enriched metadata.

    For each sub-task dict, adds the following keys:

    - ``_classification``: The ``SubTaskClassification`` object.
    - ``_task_type``: String value of the classified type.
    - ``_pipeline``: The selected pipeline name.
    - ``_confidence``: Classification confidence.
    - ``_reasoning``: Explanation of the classification decision.

    Args:
        sub_tasks: List of sub-task dicts, each expected to have at least
            a ``"description"`` key. Optional keys: ``"done_criteria"``,
            ``"deps"``, ``"dependencies"``.
        parent_complexity: Overall complexity of the parent task.

    Returns:
        The same list with enrichment keys added in place.
    """
    enriched: list[dict[str, Any]] = []

    for task in sub_tasks:
        description = task.get("description", task.get("title", ""))
        done_criteria = task.get("done_criteria", task.get("done", ""))
        deps = task.get("deps", task.get("dependencies", []))
        if isinstance(deps, str):
            deps = [deps]

        classification = classify_subtask(description, done_criteria, deps)
        pipeline = select_pipeline_for_subtask(classification, parent_complexity)

        # Update classification with the selected pipeline
        classification.suggested_pipeline = pipeline

        enriched_task = dict(task)
        enriched_task.update({
            "_classification": classification,
            "_task_type": classification.task_type.value,
            "_pipeline": pipeline,
            "_confidence": classification.confidence,
            "_reasoning": classification.reasoning,
        })
        enriched.append(enriched_task)

    _log.info(
        "Enriched %d sub-tasks: %s",
        len(enriched),
        ", ".join(
            f"{t.get('_task_type', '?')}({t.get('_pipeline', '?')})"
            for t in enriched
        ),
    )

    return enriched


# ── Reporting ───────────────────────────────────────────────


def format_pipeline_report(enriched: list[dict[str, Any]]) -> str:
    """Format a Markdown report of pipeline classification decisions.

    Args:
        enriched: Output of ``enrich_subtasks``.

    Returns:
        A Markdown-formatted report string.
    """
    if not enriched:
        return "No sub-tasks to report.\n"

    lines: list[str] = [
        "## Dynamic Pipeline Assignment Report",
        "",
        f"**Total sub-tasks:** {len(enriched)}",
        "",
    ]

    # Pipeline distribution summary
    pipeline_counts: dict[str, int] = {}
    for task in enriched:
        p = task.get("_pipeline", "unknown")
        pipeline_counts[p] = pipeline_counts.get(p, 0) + 1

    lines.append("### Pipeline Distribution")
    lines.append("")
    lines.append("| Pipeline | Count | Sub-tasks |")
    lines.append("|----------|-------|-----------|")

    for pipeline in ("minimal", "standard", "verified", "full"):
        count = pipeline_counts.get(pipeline, 0)
        if count == 0:
            continue
        task_names = [
            task.get("id", task.get("title", task.get("description", "?")))[:40]
            for task in enriched
            if task.get("_pipeline") == pipeline
        ]
        lines.append(f"| {pipeline} | {count} | {', '.join(task_names)} |")

    lines.append("")

    # Detailed per-task breakdown
    lines.append("### Per-Task Classification")
    lines.append("")

    for i, task in enumerate(enriched, 1):
        task_id = task.get("id", task.get("title", f"sub-task-{i}"))
        task_type = task.get("_task_type", "unknown")
        pipeline = task.get("_pipeline", "unknown")
        confidence = task.get("_confidence", 0.0)
        reasoning = task.get("_reasoning", "")

        lines.append(f"**{i}. {task_id}**")
        lines.append(f"- Type: `{task_type}` (confidence: {confidence:.0%})")
        lines.append(f"- Pipeline: `{pipeline}`")
        lines.append(f"- Reasoning: {reasoning}")
        lines.append("")

    return "\n".join(lines)
