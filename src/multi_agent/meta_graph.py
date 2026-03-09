"""Meta-graph — orchestrate sequential execution of decomposed sub-tasks.

Each sub-task runs through its own independent build-review cycle using
the existing 4-node LangGraph workflow. The meta-graph coordinates:
1. Topological ordering of sub-tasks by dependencies
2. Sequential execution of each sub-task
3. Context passing: previous sub-task results feed into next
4. Aggregation of all results into a final report
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from multi_agent._utils import SAFE_TASK_ID_RE as _ID_RE
from multi_agent._utils import validate_task_id as _validate_task_id
from multi_agent.schema import SubTask

_log = logging.getLogger(__name__)


def save_checkpoint(parent_task_id: str, prior_results: list[dict[str, Any]],
                    completed_ids: list[str]) -> Path:
    """Persist decompose progress to disk for crash recovery (MAS-FIRE 2026).

    Saves after each sub-task completion so progress is never lost.
    """
    _validate_task_id(parent_task_id)
    from multi_agent.config import workspace_dir
    ckpt_dir = workspace_dir() / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = ckpt_dir / f"decompose-{parent_task_id}.json"
    data = {
        "parent_task_id": parent_task_id,
        "completed_ids": completed_ids,
        "prior_results": prior_results,
    }
    # Atomic write: write to temp file then rename to prevent TOCTOU corruption
    content = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=str(ckpt_dir), suffix=".tmp")
    closed = False
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        closed = True
        os.replace(tmp_path, str(ckpt_path))
    except Exception:
        if not closed:
            with __import__('contextlib').suppress(OSError):
                os.close(fd)
        with __import__('contextlib').suppress(OSError):
            os.unlink(tmp_path)
        raise
    _log.debug("Decompose checkpoint saved: %s (%d sub-tasks done)", ckpt_path, len(completed_ids))
    return ckpt_path


_MAX_CHECKPOINT_SIZE = 10 * 1024 * 1024  # 10 MB cap


def load_checkpoint(parent_task_id: str) -> dict[str, Any] | None:
    """Load decompose checkpoint if it exists. Returns None if no checkpoint."""
    _validate_task_id(parent_task_id)
    from multi_agent.config import workspace_dir
    ckpt_path = workspace_dir() / "checkpoints" / f"decompose-{parent_task_id}.json"
    if not ckpt_path.exists():
        return None
    try:
        fsize = ckpt_path.stat().st_size
        if fsize > _MAX_CHECKPOINT_SIZE:
            _log.warning("Checkpoint too large: %d bytes > %d limit", fsize, _MAX_CHECKPOINT_SIZE)
            return None
        data = json.loads(ckpt_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "completed_ids" in data and "prior_results" in data:
            _log.info("Decompose checkpoint loaded: %d sub-tasks already done", len(data["completed_ids"]))
            return data
    except Exception as exc:
        _log.warning("Failed to load decompose checkpoint: %s", exc)
    return None


def clear_checkpoint(parent_task_id: str) -> None:
    """Remove checkpoint after successful completion."""
    _validate_task_id(parent_task_id)
    from multi_agent.config import workspace_dir
    ckpt_path = workspace_dir() / "checkpoints" / f"decompose-{parent_task_id}.json"
    if ckpt_path.exists():
        ckpt_path.unlink(missing_ok=True)


def generate_sub_task_id(parent_task_id: str, sub_id: str) -> str:
    """Generate a readable task ID for a sub-task.

    Format: task-{parent_short}-{sub_id_cleaned}
    Falls back to hash-based ID if the result doesn't match _ID_RE.
    """
    # Extract short parent name: remove "task-" prefix, take last segment
    parent_short = parent_task_id.removeprefix("task-")
    # Keep only first 12 chars of parent
    parent_short = parent_short[:12].rstrip("-")

    # Clean sub_id: lowercase, replace non-alphanumeric with hyphen
    cleaned = re.sub(r"[^a-z0-9]+", "-", sub_id.lower()).strip("-")
    cleaned = cleaned[:20].rstrip("-")

    readable = f"task-{parent_short}-{cleaned}" if parent_short else f"task-{cleaned}"
    # Collapse multiple hyphens
    readable = re.sub(r"-{2,}", "-", readable)

    if _ID_RE.match(readable):
        return readable

    # Fallback to hash-based ID
    h = hashlib.sha256(f"{parent_task_id}-{sub_id}".encode()).hexdigest()[:6]
    return f"task-{h}"


def format_prior_context(
    prior_results: list[dict[str, Any]],
    max_items: int = 3,
    dep_ids: list[str] | None = None,
) -> str:
    """Format prior sub-task results into a readable context string.

    E1 (MAST FM-5 information loss): Always includes results for dependency
    sub-tasks even if they are older than the last ``max_items``. This prevents
    critical context loss when sub-task N depends on sub-task 1's output but
    only the most recent sub-tasks would otherwise appear.

    Returns empty string if no prior results.
    """
    if not prior_results:
        return ""

    dep_set = set(dep_ids or [])
    # Include: all dependency results + most recent max_items (deduplicated)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Dependency results first (may be older)
    for pr in prior_results:
        sid = pr.get("sub_id", "")
        if sid in dep_set and sid not in seen:
            selected.append(pr)
            seen.add(sid)
    # Then recent results
    for pr in prior_results[-max_items:]:
        sid = pr.get("sub_id", "")
        if sid not in seen:
            selected.append(pr)
            seen.add(sid)

    lines = ["已完成的相关子任务:"]
    for pr in selected:
        dep_marker = " [依赖]" if pr.get("sub_id", "") in dep_set else ""
        lines.append(f"  - {pr.get('sub_id', '?')}{dep_marker}: {pr.get('summary', '?')}")
        changed = pr.get("changed_files", [])
        if changed:
            lines.append(f"    修改文件: {', '.join(changed)}")
        feedback = pr.get("reviewer_feedback", "")
        if feedback:
            lines.append(f"    Reviewer 反馈: {feedback}")
    return "\n".join(lines)


def build_sub_task_state(
    sub_task: SubTask,
    parent_task_id: str,
    builder: str = "",
    reviewer: str = "",
    timeout: int = 1800,
    retry_budget: int = 2,
    prior_results: list[dict[str, Any]] | None = None,
    workflow_mode: str = "strict",
    review_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the initial state dict for a sub-task's build-review cycle.

    prior_results: list of completed sub-task summaries for context.
    """
    task_id = generate_sub_task_id(parent_task_id, sub_task.id)

    # Build context from prior completed sub-tasks (max 3 recent + all deps)
    # E1: Always include dependency results to prevent FM-5 information loss
    context = format_prior_context(prior_results or [], dep_ids=list(sub_task.deps))

    requirement = sub_task.description
    if context:
        requirement = requirement + "\n\n" + context

    # Merge acceptance_criteria into done_criteria
    done = list(sub_task.done_criteria or [sub_task.description])
    if hasattr(sub_task, "acceptance_criteria") and sub_task.acceptance_criteria:
        done.extend(sub_task.acceptance_criteria)

    # Resolve orchestrator so it persists in graph state (same fix as cli/session)
    from multi_agent.router import get_defaults as _get_defaults
    _defaults = _get_defaults()
    _orchestrator = str(_defaults.get("orchestrator", "")).strip() or "codex"

    return {
        "task_id": task_id,
        "requirement": requirement,
        "skill_id": sub_task.skill_id,
        "done_criteria": done,
        "workflow_mode": workflow_mode,
        "review_policy": review_policy or {},
        "timeout_sec": timeout,
        "retry_budget": retry_budget,
        "retry_count": 0,
        "input_payload": {"requirement": sub_task.description},
        "builder_explicit": builder,
        "reviewer_explicit": reviewer,
        "orchestrator_id": _orchestrator,
        "conversation": [],
        "parent_task_id": parent_task_id,
    }


def aggregate_results(
    parent_task_id: str,
    sub_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate results from all completed sub-tasks into a summary."""
    all_files = []
    all_summaries = []
    total_retries = 0
    failed = []

    for sr in sub_results:
        sub_id = sr.get("sub_id", "?")
        status = sr.get("status", "unknown")
        summary = sr.get("summary", "")
        changed = sr.get("changed_files", [])
        retries = sr.get("retry_count", 0)

        all_summaries.append(f"- {sub_id}: {summary}")
        all_files.extend(changed)
        total_retries += retries

        if status not in ("approved", "completed"):
            failed.append(sub_id)

    # Task 30: duration stats
    durations = [sr.get("duration_sec", 0) for sr in sub_results]
    total_duration = sum(durations)
    avg_duration = total_duration / len(sub_results) if sub_results else 0
    slowest_idx = max(range(len(durations)), key=lambda i: durations[i]) if durations else -1
    slowest_sub = sub_results[slowest_idx].get("sub_id", "?") if slowest_idx >= 0 else ""
    slowest_dur = durations[slowest_idx] if slowest_idx >= 0 else 0

    # Task 5: estimated vs actual time comparison
    total_estimated_min = sum(sr.get("estimated_minutes", 0) for sr in sub_results)
    actual_total_min = round(total_duration / 60, 1) if total_duration else 0

    return {
        "task_id": parent_task_id,
        "total_sub_tasks": len(sub_results),
        "completed": len(sub_results) - len(failed),
        "failed": failed,
        "total_retries": total_retries,
        "all_changed_files": sorted(set(all_files)),
        "summary": "\n".join(all_summaries),
        "final_status": "failed" if failed else "approved",
        "total_duration_sec": total_duration,
        "avg_duration_sec": round(avg_duration, 1),
        "slowest_sub_task": slowest_sub,
        "slowest_duration_sec": slowest_dur,
        "estimated_total_minutes": total_estimated_min,
        "actual_total_minutes": actual_total_min,
        "sub_results": sub_results,
    }


def generate_aggregate_report(agg: dict[str, Any]) -> str:
    """Generate a Markdown report from aggregated sub-task results.

    Task 26: Returns formatted Markdown with summary table and file list.
    """
    lines = [
        "# 任务分解执行报告",
        "",
        "## 概要",
        f"- 总子任务: {agg.get('total_sub_tasks', 0)}",
        f"- 完成: {agg.get('completed', 0)}",
        f"- 失败: {len(agg.get('failed', []))}",
        f"- 总重试: {agg.get('total_retries', 0)}",
    ]

    # Duration stats (Task 30)
    total_dur = agg.get("total_duration_sec", 0)
    if total_dur > 0:
        mins, secs = divmod(int(total_dur), 60)
        lines.append(f"- 总耗时: {mins} 分 {secs} 秒")
        lines.append(f"- 平均耗时: {agg.get('avg_duration_sec', 0)} 秒")
        slowest = agg.get("slowest_sub_task", "")
        if slowest:
            lines.append(f"- 最慢子任务: {slowest} ({agg.get('slowest_duration_sec', 0):.0f} 秒)")

    # Task 5: estimated vs actual time
    est = agg.get("estimated_total_minutes", 0)
    act = agg.get("actual_total_minutes", 0)
    if est > 0:
        lines.append(f"- 预估总时间: {est} 分钟")
        lines.append(f"- 实际总时间: {act} 分钟")
        if act > 0:
            ratio = act / est
            lines.append(f"- 准确率: {ratio:.1%}")

    lines.append("")
    lines.append("## 详情")
    lines.append("")
    lines.append("| # | 子任务 | 状态 | 重试 | 摘要 |")
    lines.append("|---|--------|------|------|------|")

    sub_results = agg.get("sub_results", [])
    for i, sr in enumerate(sub_results, 1):
        sub_id = sr.get("sub_id", "?")
        status = sr.get("status", "unknown")
        retries = sr.get("retry_count", 0)
        summary = sr.get("summary", "")
        if status in ("approved", "completed"):
            emoji = "✅ 通过"
        elif status == "skipped":
            emoji = "⏭️ 跳过"
        else:
            emoji = "❌ 失败"
        lines.append(f"| {i} | {sub_id} | {emoji} | {retries} | {summary} |")

    files = agg.get("all_changed_files", [])
    if files:
        lines.append("")
        lines.append("## 修改文件")
        lines.append("")
        for f in files:
            lines.append(f"- {f}")

    lines.append("")
    return "\n".join(lines)
