"""Graph infrastructure — stats, timing, conversation trimming, snapshots, event hooks.

Extracted from graph.py (A3 refactor) to reduce module size and improve separation
of concerns. All public names are re-exported from graph.py for backward compatibility.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

_log = logging.getLogger(__name__)

MAX_SNAPSHOTS = 10
MAX_CONVERSATION_SIZE = 50


# ── Graph Stats ──────────────────────────────────────────


class GraphStats:
    """Collect graph execution statistics per node."""

    def __init__(self) -> None:
        self._stats: dict[str, dict[str, Any]] = {}
        self._retry_outcomes: list[dict[str, Any]] = []

    def record(self, node: str, duration_ms: int, success: bool) -> None:
        if node not in self._stats:
            self._stats[node] = {"count": 0, "total_ms": 0, "errors": 0}
        s = self._stats[node]
        s["count"] += 1
        s["total_ms"] += duration_ms
        if not success:
            s["errors"] += 1

    def record_retry_outcome(self, retry_round: int, decision: str) -> None:
        """Track retry effectiveness per round (DDI measurement, Nature 2025)."""
        self._retry_outcomes.append({"round": retry_round, "decision": decision})

    def record_token_usage(self, node: str, usage: dict[str, Any]) -> None:
        """Track token usage from IDE driver output (FinOps, Zylos 2026).

        ``usage`` may contain: input_tokens, output_tokens, total_tokens, cost.
        Only recorded if the IDE driver reports it — non-breaking.
        """
        if node not in self._stats:
            self._stats[node] = {"count": 0, "total_ms": 0, "errors": 0}
        s = self._stats[node]
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            val = usage.get(key)
            if isinstance(val, (int, float)):
                s[key] = s.get(key, 0) + int(val)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            s["cost"] = round(s.get("cost", 0.0) + float(cost), 6)

    def summary(self) -> dict[str, dict[str, Any]]:
        result = {}
        for node, s in self._stats.items():
            avg = s["total_ms"] / s["count"] if s["count"] else 0
            error_rate = s["errors"] / s["count"] if s["count"] else 0
            entry = {
                "count": s["count"],
                "avg_ms": round(avg),
                "error_rate": round(error_rate, 3),
            }
            # Include token usage stats if recorded (FinOps)
            for tk in ("input_tokens", "output_tokens", "total_tokens", "cost"):
                if tk in s:
                    entry[tk] = s[tk]
            result[node] = entry
        # Retry effectiveness metrics (DDI / Agentless cost tracking)
        if self._retry_outcomes:
            total_retries = len(self._retry_outcomes)
            approves = sum(1 for r in self._retry_outcomes if r["decision"] == "approve")
            result["_retry_effectiveness"] = {
                "total_retries": total_retries,
                "retry_success_rate": round(approves / total_retries, 3) if total_retries else 0,
                "per_round": self._retry_outcomes,
            }
        return result

    def cumulative_totals(self) -> dict[str, int | float]:
        """Aggregate token/cost totals across all nodes (SWE-agent cost tracking).

        Returns dict with total_tokens, input_tokens, output_tokens, cost.
        """
        totals: dict[str, int | float] = {}
        for s in self._stats.values():
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                if key in s:
                    totals[key] = totals.get(key, 0) + s[key]
            if "cost" in s:
                totals["cost"] = round(totals.get("cost", 0.0) + s["cost"], 6)
        return totals

    def warn_if_over_budget(self, max_tokens: int = 500_000) -> bool:
        """Log warning if cumulative token usage exceeds threshold.

        Inspired by SWE-agent cost limits. Returns True if over budget.
        Does NOT hard-fail — the orchestrator doesn't control LLM calls,
        but warns for observability (FinOps).
        """
        totals = self.cumulative_totals()
        used = totals.get("total_tokens", 0)
        if used > max_tokens:
            _log.warning(
                "Token budget warning: %d tokens used (threshold: %d). "
                "Consider reviewing task complexity or retry count.",
                used, max_tokens,
            )
            return True
        return False

    def reset(self) -> None:
        """Clear all accumulated stats. Call at task start to prevent cross-task contamination
        (MAST NeurIPS 2025 — system design failure mode SD-4; MAS-FIRE 2026)."""
        self._stats.clear()
        self._retry_outcomes.clear()

    def save(self, path: Any = None) -> None:
        """Save stats to .multi-agent/stats.json."""
        from multi_agent.config import workspace_dir as _ws
        p = path or (_ws() / "stats.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            p.write_text(json.dumps(self.summary(), indent=2), encoding="utf-8")


graph_stats = GraphStats()


# ── Timing ───────────────────────────────────────────────


def log_timing(task_id: str, node: str, start: float, end: float) -> None:
    """Append a timing entry to .multi-agent/logs/timing-{task_id}.jsonl."""
    from multi_agent.config import workspace_dir as _ws
    logs_dir = _ws() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "node": node,
        "start": start,
        "end": end,
        "duration_ms": int((end - start) * 1000),
    }
    # Sanitize task_id for path safety (defense-in-depth, same as save_state_snapshot)
    safe_tid = re.sub(r"[^a-zA-Z0-9._-]", "_", task_id)[:64]
    path = logs_dir / f"timing-{safe_tid}.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ── Conversation Trimming ────────────────────────────────


def trim_conversation(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep conversation within MAX_CONVERSATION_SIZE, preserving first and last entries.

    NOTE: This trims a *local copy* used for prompt rendering, dashboard display,
    and history archiving. The LangGraph internal state (Annotated[list, add])
    accumulates all entries and is NOT trimmed by this function. For long-running
    tasks, this distinction matters: prompts stay bounded, but the checkpoint DB
    may grow large (Context Rot — Chroma/Hong et al. 2025).

    Includes a lightweight summary of removed entries so downstream AI agents
    retain context awareness (literature: JetBrains context management research).
    """
    if len(conversation) <= MAX_CONVERSATION_SIZE:
        return conversation
    keep_head = 5
    keep_tail = MAX_CONVERSATION_SIZE - keep_head - 1
    removed = conversation[keep_head:-keep_tail]
    # Build lightweight summary of what was removed.
    # Keep ALL retry/request_changes feedback (not just 3) to prevent
    # critical bug context loss (RepairAgent ICSE 2025, Redis 2026).
    action_counts: dict[str, int] = {}
    feedback_snippets: list[str] = []
    for e in removed:
        a = e.get("action", "unknown")
        action_counts[a] = action_counts.get(a, 0) + 1
        fb = e.get("feedback", "")
        if fb and isinstance(fb, str) and a in ("retry", "request_changes"):
            feedback_snippets.append(fb[:120])
    summary_parts = [f"{a}×{c}" for a, c in action_counts.items()]
    trimmed_marker = {
        "role": "system", "action": "trimmed",
        "details": f"Removed {len(removed)} entries: {', '.join(summary_parts)}",
        "key_feedback": feedback_snippets,
        "t": time.time(),
    }
    return [*conversation[:keep_head], trimmed_marker, *conversation[-keep_tail:]]


# ── State Snapshots ──────────────────────────────────────


def save_state_snapshot(task_id: str, node_name: str, state: dict[str, Any]) -> None:
    """Save a state snapshot for debugging. Keeps only the latest MAX_SNAPSHOTS."""
    from multi_agent.config import workspace_dir as _ws_dir
    snap_dir = _ws_dir() / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # G2: Sanitize task_id/node_name to prevent path traversal
    # (e.g. task_id="../../etc/passwd" would write outside snapshots dir)
    safe_tid = re.sub(r"[^a-zA-Z0-9._-]", "_", task_id)[:64]
    safe_node = re.sub(r"[^a-zA-Z0-9._-]", "_", node_name)[:32]

    ts = int(time.time() * 1000)
    safe_state = {}
    for k, v in state.items():
        try:
            json.dumps(v)
            safe_state[k] = v
        except (TypeError, ValueError):
            safe_state[k] = str(v)

    path = snap_dir / f"{safe_tid}-{safe_node}-{ts}.json"
    try:
        path.write_text(json.dumps(safe_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return

    # Cleanup old snapshots — sort by filename (embeds timestamp) for reliability
    try:
        existing = sorted(snap_dir.glob(f"{safe_tid}-*.json"), key=lambda p: p.name)
        while len(existing) > MAX_SNAPSHOTS:
            existing.pop(0).unlink(missing_ok=True)
    except OSError:
        pass


# ── Event Hooks ──────────────────────────────────────────

_hook_logger = logging.getLogger(__name__ + ".hooks")


class EventHooks:
    """Registry for graph execution event callbacks.

    Usage:
        hooks = EventHooks()
        hooks.on_node_enter("plan", lambda state: print("plan started"))
        hooks.on_node_exit("build", lambda state, result: log(result))
        hooks.on_error(lambda node, state, err: alert(err))
    """

    def __init__(self) -> None:
        self._enter: dict[str, list[Any]] = {}   # node_name → [callbacks]
        self._exit: dict[str, list[Any]] = {}    # node_name → [callbacks]
        self._error: list[Any] = []              # global error handlers

    def on_node_enter(self, node: str, callback: Any) -> None:
        self._enter.setdefault(node, []).append(callback)

    def on_node_exit(self, node: str, callback: Any) -> None:
        self._exit.setdefault(node, []).append(callback)

    def on_error(self, callback: Any) -> None:
        self._error.append(callback)

    def fire_enter(self, node: str, state: Mapping[str, Any]) -> None:
        for cb in self._enter.get(node, []):
            try:
                cb(state)
            except Exception as e:
                _hook_logger.warning("Hook enter/%s error: %s", node, e)

    def fire_exit(self, node: str, state: Mapping[str, Any], result: dict[str, Any] | None = None) -> None:
        for cb in self._exit.get(node, []):
            try:
                cb(state, result)
            except Exception as e:
                _hook_logger.warning("Hook exit/%s error: %s", node, e)

    def fire_error(self, node: str, state: Mapping[str, Any], error: Exception) -> None:
        for cb in self._error:
            try:
                cb(node, state, error)
            except Exception as e:
                _hook_logger.warning("Hook error handler error: %s", e)


# Global hooks instance — importable by consumers
graph_hooks = EventHooks()


def register_hook(event: str, callback: Callable[..., Any]) -> None:
    """Register a callback for a graph event (public API).

    Supported events: plan_start, build_submit, review_submit,
    decide_approve, decide_reject, task_failed.
    Maps to EventHooks.on_node_enter/on_node_exit internally.
    """
    _event_map = {
        "plan_start": ("enter", "plan"),
        "build_submit": ("exit", "build"),
        "review_submit": ("exit", "review"),
        "decide_approve": ("exit", "decide"),
        "decide_reject": ("exit", "decide"),
        "task_failed": ("error", None),
    }
    mapping = _event_map.get(event)
    if mapping is None:
        graph_hooks.on_node_enter(event, callback)
        return
    kind, node = mapping
    if kind == "enter" and node is not None:
        graph_hooks.on_node_enter(node, callback)
    elif kind == "exit" and node is not None:
        graph_hooks.on_node_exit(node, callback)
    elif kind == "error":
        graph_hooks.on_error(callback)
