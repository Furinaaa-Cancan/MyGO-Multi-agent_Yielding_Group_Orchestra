"""FinOps — Token usage tracking, cost calculation, and budget alerts.

Persists per-task token usage to JSONL files in ``.multi-agent/logs/token-usage.jsonl``.
Aggregates stats across tasks for CLI reporting and Dashboard visualization.

Pricing data based on common LLM API rates (configurable via ``.ma.yaml``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from multi_agent.config import workspace_dir

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    _fcntl = None  # type: ignore[assignment]
fcntl = _fcntl

_log = logging.getLogger(__name__)

# ── Default Model Pricing (USD per 1M tokens) ────────────
# Users can override via .ma.yaml finops.pricing section
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3": {"input": 2.00, "output": 8.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3.5-haiku": {"input": 0.80, "output": 4.00},
    "codex": {"input": 2.50, "output": 10.00},
    "default": {"input": 2.50, "output": 10.00},
}

# ── Token Usage File ─────────────────────────────────────

_MAX_USAGE_FILE_SIZE = 10 * 1024 * 1024  # 10 MB cap


def _usage_file() -> Path:
    return workspace_dir() / "logs" / "token-usage.jsonl"


def record_task_usage(
    *,
    task_id: str,
    node: str,
    agent_id: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cost: float = 0.0,
    model: str = "",
) -> None:
    """Append a token usage entry to the persistent log."""
    path = _usage_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": time.time(),
        "task_id": task_id,
        "node": node,
        "agent_id": agent_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or (input_tokens + output_tokens),
        "cost": round(cost, 6),
        "model": model,
    }

    try:
        with path.open("a", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            finally:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError as e:
        _log.warning("Failed to write token usage: %s", e)


def load_usage_log() -> list[dict[str, Any]]:
    """Load all token usage entries from the persistent log."""
    path = _usage_file()
    if not path.exists():
        return []
    try:
        fsize = path.stat().st_size
        if fsize > _MAX_USAGE_FILE_SIZE:
            _log.warning("Token usage log too large: %d bytes", fsize)
            return []
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return entries


# ── Cost Calculation ─────────────────────────────────────

def estimate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = "default",
    pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Estimate cost in USD for given token counts and model.

    Uses DEFAULT_PRICING unless custom pricing dict is provided.
    Falls back to 'default' pricing if model not found.
    """
    prices = pricing or DEFAULT_PRICING
    model_price = prices.get(model, prices.get("default", {"input": 2.5, "output": 10.0}))
    cost = (input_tokens * model_price["input"] + output_tokens * model_price["output"]) / 1_000_000
    return round(cost, 6)


# ── Aggregation & Reporting ──────────────────────────────

def aggregate_usage(
    entries: list[dict[str, Any]] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate token usage stats, optionally filtered by task_id.

    Returns:
        {
            "total_tokens": int,
            "input_tokens": int,
            "output_tokens": int,
            "total_cost": float,
            "task_count": int,
            "by_task": { task_id: { ... } },
            "by_node": { node: { ... } },
            "by_agent": { agent_id: { ... } },
        }
    """
    if entries is None:
        entries = load_usage_log()

    if task_id:
        entries = [e for e in entries if e.get("task_id") == task_id]

    totals = {
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_cost": 0.0,
        "entry_count": len(entries),
    }
    by_task: dict[str, dict[str, Any]] = {}
    by_node: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    task_ids: set[str] = set()

    for e in entries:
        inp = int(e.get("input_tokens", 0))
        out = int(e.get("output_tokens", 0))
        tot = int(e.get("total_tokens", 0)) or (inp + out)
        cost = float(e.get("cost", 0.0))
        tid = str(e.get("task_id", "unknown"))
        node = str(e.get("node", "unknown"))
        agent = str(e.get("agent_id", "unknown"))
        task_ids.add(tid)

        totals["total_tokens"] += tot
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["total_cost"] += cost

        for bucket, key in [(by_task, tid), (by_node, node), (by_agent, agent)]:
            if key not in bucket:
                bucket[key] = {"total_tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "count": 0}
            b = bucket[key]
            b["total_tokens"] += tot
            b["input_tokens"] += inp
            b["output_tokens"] += out
            b["cost"] += cost
            b["count"] += 1

    totals["total_cost"] = round(totals["total_cost"], 6)
    totals["task_count"] = len(task_ids)

    # Round cost in sub-dicts
    for bucket in (by_task, by_node, by_agent):
        for v in bucket.values():
            v["cost"] = round(v["cost"], 6)

    return {**totals, "by_task": by_task, "by_node": by_node, "by_agent": by_agent}


def format_report(agg: dict[str, Any] | None = None) -> str:
    """Format a human-readable FinOps report."""
    if agg is None:
        agg = aggregate_usage()

    lines = [
        "╔══════════════════════════════════════════╗",
        "║     MyGO FinOps — Token Usage Report     ║",
        "╚══════════════════════════════════════════╝",
        "",
        f"  Total Tokens:   {agg['total_tokens']:>12,}",
        f"    Input:        {agg['input_tokens']:>12,}",
        f"    Output:       {agg['output_tokens']:>12,}",
        f"  Estimated Cost: ${agg['total_cost']:>11,.4f}",
        f"  Tasks:          {agg['task_count']:>12}",
        f"  Records:        {agg['entry_count']:>12}",
    ]

    by_node = agg.get("by_node", {})
    if by_node:
        lines.append("")
        lines.append("  ── By Node ────────────────────────")
        for node, s in sorted(by_node.items(), key=lambda x: x[1]["total_tokens"], reverse=True):
            lines.append(f"    {node:<12} {s['total_tokens']:>10,} tokens  ${s['cost']:>8,.4f}")

    by_task = agg.get("by_task", {})
    if by_task:
        lines.append("")
        lines.append("  ── By Task (top 10) ───────────────")
        for tid, s in sorted(by_task.items(), key=lambda x: x[1]["total_tokens"], reverse=True)[:10]:
            lines.append(f"    {tid:<28} {s['total_tokens']:>10,} tokens  ${s['cost']:>8,.4f}")

    by_agent = agg.get("by_agent", {})
    if by_agent:
        lines.append("")
        lines.append("  ── By Agent ───────────────────────")
        for agent, s in sorted(by_agent.items(), key=lambda x: x[1]["total_tokens"], reverse=True):
            if agent and agent != "unknown":
                lines.append(f"    {agent:<16} {s['total_tokens']:>10,} tokens  ${s['cost']:>8,.4f}")

    lines.append("")
    return "\n".join(lines)


# ── Budget Alert ─────────────────────────────────────────

def check_budget(
    max_cost: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Check if usage exceeds budget thresholds.

    Returns a dict with 'over_budget', 'warnings', and current totals.
    Budget thresholds from .ma.yaml finops section:
        finops:
          budget_usd: 10.0
          budget_tokens: 1000000
    """
    if max_cost is None and max_tokens is None:
        try:
            from multi_agent.config import load_project_config
            cfg = load_project_config()
            finops_cfg = cfg.get("finops", {})
            if isinstance(finops_cfg, dict):
                max_cost = finops_cfg.get("budget_usd")
                max_tokens = finops_cfg.get("budget_tokens")
        except Exception:
            pass

    agg = aggregate_usage()
    warnings: list[str] = []

    if max_cost is not None and agg["total_cost"] > max_cost:
        warnings.append(f"Cost ${agg['total_cost']:.4f} exceeds budget ${max_cost:.4f}")
    if max_tokens is not None and agg["total_tokens"] > max_tokens:
        warnings.append(f"Tokens {agg['total_tokens']:,} exceed budget {max_tokens:,}")

    for w in warnings:
        _log.warning("FinOps budget alert: %s", w)

    return {
        "over_budget": len(warnings) > 0,
        "warnings": warnings,
        "total_tokens": agg["total_tokens"],
        "total_cost": agg["total_cost"],
        "budget_usd": max_cost,
        "budget_tokens": max_tokens,
    }
